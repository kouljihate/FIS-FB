"""Dataset used by the Playwright suite.

Creates, against the test database only:

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
import os
import re
from datetime import datetime
from pathlib import Path

import requests
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
DB_NAME = 'FIS_playwright'
MONGO_URI = os.environ.get('MONGO_URI') or f'mongodb://127.0.0.1:27017/{DB_NAME}'

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


def reset_database() -> None:
    """Drop the test database so every run starts from nothing."""
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)
    client.drop_database(DB_NAME)


def _db():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)[DB_NAME]


def _bootstrap_admin(db) -> None:
    """Insert the first Admin directly: the UI has no way to create one."""
    db.user.insert_one({
        'id': 1,
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


def _create_project(session, base_url, *, name, admin_id, location, boundary):
    data = [
        ('name', name),
        ('location', location),
        ('description', 'Playwright end-to-end project'),
        ('admin_user_id', str(admin_id)),
        ('water_source_lat', str(WATER_SOURCE[0])),
        ('water_source_lng', str(WATER_SOURCE[1])),
        ('boundary_coords', json.dumps(boundary)),
        ('zones_per_sector', '2'),
        ('row_spacing_m', '3.0'),
        ('tree_spacing_m', '4.0'),
        ('emitter_flow_lph', '2.0'),
        ('min_sector_area_m2', '5000'),
        ('max_sector_area_m2', '50000'),
        ('owner_name', 'Playwright Owner'),
        ('owner_email', 'owner@example.test'),
        ('owner_phone', '00000000'),
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
            owner_session = _session(base_url, owner_username, OWNER_PASSWORD)
            _create_project(owner_session, base_url, name=project_name,
                            admin_id=project_admin['id'],
                            location=f'Azguar {a_index}.{o_index}',
                            boundary=BOUNDARY)
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
    parser.add_argument('--reset', action='store_true')
    args = parser.parse_args()
    if args.reset:
        reset_database()
    print(json.dumps(build_dataset(args.base_url), indent=2))
