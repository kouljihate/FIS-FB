"""Dataset used by the Playwright suite.

The suite writes into the same MongoDB database as the application itself, so
every account, project and uploaded file it creates is visible in the running
app. It therefore never drops the database: it only deletes the fixtures it
owns - the accounts named ``pw_*``, and their projects.

Creates:

* one Admin account (bootstrapped, because no account can exist otherwise),
* three Project Admins (the "local admins", each with a company agreement),
* three Project Owners per Project Admin (nine customers),
* one project per owner, each linked to its owner's Project Admin.

Accounts, users and projects are created through the app's own HTTP endpoints,
exactly as the Users and Create Project pages do; the only direct database
write is the first Admin, which the UI cannot create.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]


def _app_config():
    """The application configuration, so the suite uses its database and folder."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from config import Config
    except Exception:  # pragma: no cover - configuration always imports
        return None
    return Config


def _app_mongo_uri() -> str:
    config = _app_config()
    return getattr(config, 'MONGO_URI', None) or 'mongodb://localhost:27017/FIS'


def uploads_root() -> Path:
    config = _app_config()
    folder = getattr(config, 'UPLOAD_FOLDER', None) or str(ROOT / 'uploads')
    return Path(folder)


#: The URI and database of the installed application (``config.Config``).
MONGO_URI = _app_mongo_uri()
DB_NAME = parse_uri(MONGO_URI).get('database') or 'FIS'

#: Names the suite owns. Nothing else in the database is ever deleted.
FIXTURE_USERNAME = re.compile(r'^pw_(?:admin|localadmin_\d+|owner_\d+_\d+)$')
FIXTURE_PROJECT = re.compile(r'^PW Project \d+\.\d+$')

ADMIN = {'username': 'pw_admin', 'password': 'AdminPass#2026',
         'email': 'pw_admin@example.test', 'full_name': 'Playwright Admin'}
LOCAL_PASSWORD = 'LocalAdmin#2026'
OWNER_PASSWORD = 'Customer#2026'

COMPANY_NAMES = ['Azguar Estate', 'Atlas Orchards', 'Souss Groves']

#: Small square of land (~160 m x 160 m): enough for four sectors and a few
#: thousand trees, small enough to keep the map pages quick under automation.
BOUNDARY = [
    [-4.7296, 33.6159],
    [-4.7279, 33.6159],
    [-4.7279, 33.6173],
    [-4.7296, 33.6173],
    [-4.7296, 33.6159],
]
WATER_SOURCE = (33.6162, -4.7294)


def _pdf_bytes() -> bytes:
    """A one-page PDF, enough to exercise the agreement upload."""
    return (b'%PDF-1.4\n'
            b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
            b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
            b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n'
            b'trailer<</Root 1 0 R>>\n%%EOF\n')


def elevation_maps() -> list[Path]:
    """Every KML in maps/ whose name mentions elevation, in natural order."""
    files = [p for p in (ROOT / 'maps').glob('*.kml') if 'elevation' in p.name.lower()]

    def key(path: Path):
        return [int(part) if part.isdigit() else part.lower()
                for part in re.split(r'(\d+)', path.name)]

    return sorted(files, key=key)


def purge_seeded() -> dict:
    """Delete the accounts and projects this suite created, and nothing else.

    The database is shared with the real installation, so it is never dropped:
    only the ``pw_*`` accounts, the projects they own and the files those
    uploaded are removed, which makes every run start from the same state.
    """
    db = _db()
    users = list(db.user.find({}, {'id': 1, 'username': 1}))
    user_ids = [u['id'] for u in users if FIXTURE_USERNAME.match(u.get('username') or '')]
    also_removed = set(user_ids)
    survivor_ids = {u['id'] for u in users if u['id'] not in also_removed}

    projects = list(db.project.find({}, {'id': 1, 'user_id': 1, 'name': 1}))
    project_ids = [
        p['id'] for p in projects
        if p.get('user_id') in also_removed
        # A project left behind by an interrupted run whose owner is already gone.
        or (FIXTURE_PROJECT.match(p.get('name') or '')
            and p.get('user_id') not in survivor_ids)
    ]

    if project_ids:
        db.project.delete_many({'id': {'$in': project_ids}})
    if user_ids:
        db.user.delete_many({'id': {'$in': user_ids}})
    _drop_uploaded_files(user_ids, project_ids)
    return {'users': len(user_ids), 'projects': len(project_ids)}


def _drop_uploaded_files(user_ids, project_ids) -> None:
    """Remove the KML files, agreements and photos of the deleted fixtures."""
    root = uploads_root()
    for user_id in user_ids:
        for folder in ('photos', 'agreements'):
            for path in (root / folder).glob(f'user_{user_id}_*'):
                try:
                    path.unlink()
                except OSError:  # pragma: no cover - file already gone
                    pass
    for project_id in project_ids:
        for path in root.glob(f'project_{project_id}_*'):
            try:
                path.unlink()
            except OSError:  # pragma: no cover
                pass


def _db():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)[DB_NAME]


def _next_user_id(db) -> int:
    """Next free integer id, the same way the application allocates it."""
    best = 0
    for doc in db.user.find({}, {'id': 1}):
        try:
            best = max(best, int(doc.get('id') or 0))
        except (TypeError, ValueError):
            continue
    return best + 1


def _bootstrap_admin(db) -> None:
    """Insert the first Admin directly: the UI has no way to create one."""
    db.user.insert_one({
        'id': _next_user_id(db),
        'full_name': ADMIN['full_name'],
        'username': ADMIN['username'],
        'email': ADMIN['email'],
        'phone': '',
        'password_hash': generate_password_hash(ADMIN['password']),
        'profile': 'Admin',
        'cin': '',
        'photo': None,
        'is_active': True,
        'created_at': datetime.utcnow(),
    })


def _session(base_url: str, username: str, password: str) -> requests.Session:
    session = requests.Session()
    response = session.post(base_url + '/login',
                            data={'username': username, 'password': password},
                            timeout=60)
    assert response.status_code == 200, f'login {username} -> HTTP {response.status_code}'
    assert 'Invalid credentials' not in response.text, f'login failed for {username}'
    return session


def _create_user(session, base_url, *, full_name, username, email, password,
                 profile, cin, phone='', company=None):
    data = {
        'full_name': full_name,
        'username': username,
        'email': email,
        'phone': phone,
        'cin': cin,
        'password': password,
        'profile': profile,
    }
    files = None
    if company:
        data.update({
            'company_name': company['name'],
            'agreement_number': company['agreement_number'],
            'agreement_date': company['agreement_date'],
        })
        files = {'agreement_file': ('agreement.pdf', _pdf_bytes(), 'application/pdf')}
    response = session.post(base_url + '/users/create', data=data, files=files, timeout=60)
    assert response.status_code == 200, f'create user {username} -> HTTP {response.status_code}'
    assert 'User created' in response.text, f'create user {username} was refused'


def _create_project(session, base_url, *, name, admin_id, location, boundary,
                    owner_id=None):
    # A Project Owner cannot create a project: the Project Admin creates it and
    # links the owner, exactly like the application does.
    data = [
        ('name', name),
        ('location', location),
        ('description', 'Playwright end-to-end project'),
        ('admin_user_id', str(admin_id)),
        ('owner_user_id', str(owner_id) if owner_id else ''),
        ('water_source_lat', str(WATER_SOURCE[0])),
        ('water_source_lng', str(WATER_SOURCE[1])),
        ('boundary_coords', json.dumps(boundary)),
        ('zones_per_sector', '2'),
        ('row_spacing_m', '3.0'),
        ('tree_spacing_m', '4.0'),
        ('emitter_flow_lph', '2.0'),
        ('min_sector_area_m2', '5000'),
        ('max_sector_area_m2', '50000'),
        # No owner_name/e-mail here on purpose: filled Owner fields now create
        # a brand-new Project Owner account and win over this dropdown, while
        # the suite wants to link the owner account it created itself.
    ]
    response = session.post(base_url + '/create', data=data, timeout=120)
    assert response.status_code == 200, f'create project {name} -> HTTP {response.status_code}'
    assert 'Project created' in response.text, f'create project {name} was refused'


def build_dataset(base_url: str) -> dict:
    """Create users and projects, then return everything the tests need."""
    base_url = base_url.rstrip('/')
    db = _db()
    _bootstrap_admin(db)
    admin = _session(base_url, ADMIN['username'], ADMIN['password'])

    project_admins: list[dict] = []
    owners: list[dict] = []
    projects: list[dict] = []

    for a_index, company_name in enumerate(COMPANY_NAMES, start=1):
        admin_username = f'pw_localadmin_{a_index}'
        _create_user(
            admin, base_url,
            full_name=f'Local Admin {a_index}',
            username=admin_username,
            email=f'{admin_username}@example.test',
            password=LOCAL_PASSWORD,
            profile='Project Admin',
            cin=f'PA{a_index:03d}',
            phone=f'+21600000{a_index:02d}',
            company={
                'name': company_name,
                'agreement_number': f'AGR-{a_index:03d}',
                'agreement_date': '2026-01-15',
            },
        )
        admin_doc = db.user.find_one({'username': admin_username})
        assert admin_doc, f'Project Admin {admin_username} not stored'
        project_admin = {
            'id': admin_doc['id'],
            'username': admin_username,
            'password': LOCAL_PASSWORD,
            'company': company_name,
            'full_name': admin_doc.get('full_name'),
            'owners': [],
        }
        project_admins.append(project_admin)
        admin_session = None

        for o_index in range(1, 4):
            owner_username = f'pw_owner_{a_index}_{o_index}'
            _create_user(
                admin, base_url,
                full_name=f'Customer {a_index}.{o_index}',
                username=owner_username,
                email=f'{owner_username}@example.test',
                password=OWNER_PASSWORD,
                profile='Project Owner',
                cin=f'CU{a_index}{o_index:02d}',
                phone=f'+21611100{a_index}{o_index}',
            )
            owner_doc = db.user.find_one({'username': owner_username})
            assert owner_doc, f'owner {owner_username} not stored'
            owner = {
                'id': owner_doc['id'],
                'username': owner_username,
                'password': OWNER_PASSWORD,
                'admin_id': project_admin['id'],
                'full_name': owner_doc.get('full_name'),
            }
            owners.append(owner)
            project_admin['owners'].append(owner)

            project_name = f'PW Project {a_index}.{o_index}'
            if admin_session is None:
                admin_session = _session(base_url, admin_username, LOCAL_PASSWORD)
            _create_project(admin_session, base_url, name=project_name,
                            admin_id=project_admin['id'],
                            location=f'Azguar {a_index}.{o_index}',
                            boundary=BOUNDARY, owner_id=owner['id'])
            project_doc = db.project.find_one({'name': project_name})
            assert project_doc, f'project {project_name} not stored'
            projects.append({
                'id': project_doc['id'],
                'name': project_name,
                'owner': owner_username,
                'owner_id': owner['id'],
                'admin_id': project_admin['id'],
                'admin_username': admin_username,
            })

    maps = elevation_maps()

    dataset = {
        'base_url': base_url,
        'admin': ADMIN,
        'project_admins': project_admins,
        'owners': owners,
        'projects': projects,
        'maps': [str(path) for path in maps],
    }
    return dataset


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Seed the Playwright dataset')
    parser.add_argument('--base-url', default='http://127.0.0.1:7099')
    parser.add_argument('--purge', action='store_true',
                        help='delete the pw_* fixtures before seeding')
    args = parser.parse_args()
    if args.purge:
        print('purged:', purge_seeded())
    print(json.dumps(build_dataset(args.base_url), indent=2))
