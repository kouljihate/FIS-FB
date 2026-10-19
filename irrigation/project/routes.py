import os
import re
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from irrigation.access import (ROLE_PROJECT_ADMIN, find_project,
                               project_query, require_project)
from irrigation.extensions import mongo
from irrigation.models import get_next_id, serialize_doc
from irrigation.utils.kml import parse_kml_content, parse_kmz_file, export_project_to_kml
from bson import ObjectId

project_bp = Blueprint('project', __name__)


def _empty_components():
    return {
        'valves': [],
        'connectors': [],
        'reductions': [],
        'air_removal': [],
        'pressure_reduction': [],
        'terminators': [],
    }


def _land(project):
    """Land section of the two-collection model (kept in sync with the app)."""
    spec = _get_spec(project)
    return {
        'map': project.get('boundary'),
        'kml_file': project.get('kml_file'),
        'kml_features': project.get('kml_features') or [],
        'documents': spec.get('land_documents') or [],
    }


def _default_spec():
    """Default project specification (owner, configuration, rules, tree plan)."""
    return {
        'owner': {'name': '', 'email': '', 'phone': ''},
        'config': {
            'zones_per_sector': 2,
            'row_spacing_m': 3.0,
            'emitter_flow_lph': 2.0,
            'emitters_per_tree': 2,
        },
        'rules': {
            'area_per_sector_m2': 10000,
            'min_sector_area_m2': 5000,
            'max_sector_area_m2': 50000,
            'min_zones_per_sector': 1,
            'max_zones_per_sector': 8,
        },
        'tree_plan': [
            {'variety_en': 'Olive', 'variety_ar': 'زيتون', 'percentage': 60, 'scope': 'per_sector', 'tree_spacing_m': 5.0},
            {'variety_en': 'Almond', 'variety_ar': 'لوز', 'percentage': 40, 'scope': 'per_sector', 'tree_spacing_m': 6.0},
        ],
        'land_documents': [],
        'water_sources': [],
    }


def _deep_merge(defaults, override):
    """Merge override dict into defaults recursively (keeps default keys)."""
    result = dict(defaults)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_spec(project):
    spec = _default_spec()
    if project and project.get('spec'):
        spec = _deep_merge(spec, project['spec'])
    return spec


def _spec_from_form(request):
    """Build a spec dict from the project settings form."""
    def f(name, cast=str, default=None):
        value = request.form.get(name, '').strip()
        if not value:
            return default
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    varieties_en = request.form.getlist('tree_variety_en')
    varieties_ar = request.form.getlist('tree_variety_ar')
    percentages = request.form.getlist('tree_percentage')
    scopes = request.form.getlist('tree_scope')
    tree_spacings = request.form.getlist('tree_spacing')

    tree_plan = []
    for i, en in enumerate(varieties_en):
        en = en.strip()
        if not en:
            continue
        spacing = tree_spacings[i].strip() if i < len(tree_spacings) else ''
        try:
            spacing = float(spacing) if spacing else 0
        except ValueError:
            spacing = 0
        tree_plan.append({
            'variety_en': en,
            'variety_ar': varieties_ar[i].strip() if i < len(varieties_ar) else '',
            'percentage': percentages[i].strip() if i < len(percentages) else '',
            'scope': scopes[i].strip() if i < len(scopes) else 'per_sector',
            'tree_spacing_m': spacing,
        })

    # Water sources (wells, basins, other)
    ws_types = request.form.getlist('ws_type')
    ws_names = request.form.getlist('ws_name')
    ws_lats = request.form.getlist('ws_lat')
    ws_lngs = request.form.getlist('ws_lng')
    ws_flows = request.form.getlist('ws_flow_lpm')
    ws_notes = request.form.getlist('ws_notes')
    water_sources = []
    for i, wtype in enumerate(ws_types):
        wtype = wtype.strip().lower()
        name = ws_names[i].strip() if i < len(ws_names) else ''
        lat_s = ws_lats[i].strip() if i < len(ws_lats) else ''
        lng_s = ws_lngs[i].strip() if i < len(ws_lngs) else ''
        if not wtype and not name and not lat_s:
            continue
        try:
            lat = float(lat_s) if lat_s else None
        except ValueError:
            lat = None
        try:
            lng = float(lng_s) if lng_s else None
        except ValueError:
            lng = None
        flow_s = ws_flows[i].strip() if i < len(ws_flows) else ''
        try:
            flow = float(flow_s) if flow_s else None
        except ValueError:
            flow = None
        water_sources.append({
            'type': wtype or 'other',
            'name': name,
            'lat': lat,
            'lng': lng,
            'flow_lpm': flow,
            'notes': ws_notes[i].strip() if i < len(ws_notes) else '',
        })

    return {
        'owner': {
            'name': f('owner_name'),
            'email': f('owner_email'),
            'phone': f('owner_phone'),
        },
        'config': {
            'zones_per_sector': f('zones_per_sector', int, 2),
            'row_spacing_m': f('row_spacing_m', float, 3.0),
            'emitter_flow_lph': f('emitter_flow_lph', float, 2.0),
            'emitters_per_tree': f('emitters_per_tree', int, 2),
        },
        'rules': {
            'area_per_sector_m2': f('area_per_sector_m2', int, 10000),
            'min_sector_area_m2': f('min_sector_area_m2', int, 5000),
            'max_sector_area_m2': f('max_sector_area_m2', int, 50000),
            'min_zones_per_sector': f('min_zones_per_sector', int, 1),
            'max_zones_per_sector': f('max_zones_per_sector', int, 8),
        },
        'tree_plan': tree_plan or _default_spec()['tree_plan'],
        'water_sources': water_sources,
    }


def _project_admins():
    """Active users with the Project Admin profile, sorted by name."""
    return list(mongo.db.users.find({
        'profile': ROLE_PROJECT_ADMIN,
        'is_active': True,
    }).sort('full_name', 1))


def _admin_from_form():
    """Project Admin selected in the request, or ``None`` if not valid."""
    raw = request.form.get('admin_user_id', '').strip()
    if not raw.isdigit():
        return None
    return mongo.db.users.find_one({
        'id': int(raw),
        'profile': ROLE_PROJECT_ADMIN,
        'is_active': True,
    })


def _default_admin_id(admins):
    """Admin to preselect: the one just created, else the current user, else
    the first available Project Admin."""
    ids = [admin['id'] for admin in admins]
    requested = request.args.get('admin_user_id', '').strip()
    if requested.isdigit() and int(requested) in ids:
        return int(requested)
    if getattr(current_user, 'profile', None) == ROLE_PROJECT_ADMIN and int(current_user.id) in ids:
        return int(current_user.id)
    return ids[0] if ids else None


def _land_documents_from_request(project_id, previous_docs=None):
    """Parse the Land Documents rows (category/name/reference/notes/file) from
    the settings form, save uploaded files, and return the new document list."""
    import datetime

    categories = request.form.getlist('doc_category')
    names = request.form.getlist('doc_name')
    references = request.form.getlist('doc_reference')
    notes = request.form.getlist('doc_notes')
    files = request.files.getlist('doc_file')

    upload_dir = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    docs = []
    for i, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        entry = {
            'category': (categories[i].strip().lower() if i < len(categories) else 'land') or 'land',
            'name': name,
            'reference': references[i].strip() if i < len(references) else '',
            'notes': notes[i].strip() if i < len(notes) else '',
            'file': None,
            'file_original': None,
        }
        if i < len(files) and files[i] and files[i].filename:
            original = secure_filename(files[i].filename)
            stored = f'project_{project_id}_{int(datetime.datetime.utcnow().timestamp())}_{original}'
            os.makedirs(upload_dir, exist_ok=True)
            files[i].save(os.path.join(upload_dir, stored))
            entry['file'] = stored
            entry['file_original'] = files[i].filename
        docs.append(entry)

    # Delete stored files that are no longer referenced by any kept document.
    kept_files = {d['file'] for d in docs if d.get('file')}
    for old in (previous_docs or []):
        old_file = old.get('file')
        if old_file and old_file not in kept_files:
            try:
                os.remove(os.path.join(upload_dir, old_file))
            except OSError:
                pass
    return docs


@project_bp.route('/')
@login_required
def index():
    projects = list(mongo.db.projects.find(project_query()).sort('created_at', -1))
    owner_ids = {int(p['user_id']) for p in projects if p.get('user_id')}
    owners = {
        user['id']: user.get('full_name') or user.get('username') or ''
        for user in mongo.db.users.find({'id': {'$in': list(owner_ids)}})
    }
    for p in projects:
        spec_owner = (p.get('spec') or {}).get('owner') or {}
        p['owner_name'] = (
            owners.get(int(p.get('user_id') or 0))
            or spec_owner.get('name')
            or ''
        )
        p['_id'] = str(p['_id'])
    return render_template('project/index.html', projects=projects)


@project_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        project_admin = _admin_from_form()
        if not project_admin:
            flash('Select a Project Admin for the project / اختر مسؤولاً للمشروع', 'danger')
            return redirect(url_for('project.create'))

        name = request.form.get('name', '').strip()
        location = request.form.get('location', '').strip()
        description = request.form.get('description', '').strip()
        water_source_lat = request.form.get('water_source_lat', '').strip()
        water_source_lng = request.form.get('water_source_lng', '').strip()

        # Manual land boundary coordinates
        boundary_coords = request.form.get('boundary_coords', '').strip()

        spec = _spec_from_form(request)

        project_doc = {
            'id': get_next_id('projects'),
            'user_id': int(current_user.id),
            'admin_user_id': project_admin['id'],
            'name': name,
            'location': location,
            'description': description,
            'water_source': {},
            'boundary': None,
            'kml_file': None,
            'status': 'created',
            'spec': spec,
            'land': {'map': None, 'kml_file': None, 'kml_features': [], 'documents': []},
            'water': spec.get('water_sources') or [],
            'network': {'pipes': [], 'components': _empty_components()},
            'sectors': [],
            'reports': {},
            'elevation_models': [],
            'created_at': __import__('datetime').datetime.utcnow()
        }

        if water_source_lat and water_source_lng:
            lat, lng = float(water_source_lat), float(water_source_lng)
            project_doc['water_source'] = {
                'type': 'Point',
                'coordinates': [lng, lat]
            }
            spec['water_sources'] = [{
                'type': 'well',
                'name': 'Water source',
                'lat': lat,
                'lng': lng,
                'flow_lpm': None,
                'notes': 'Created with the project / أُنشئ مع المشروع',
            }]
            project_doc['spec'] = spec
            project_doc['water'] = spec['water_sources']

        if boundary_coords:
            try:
                import json
                coords = json.loads(boundary_coords)
                project_doc['boundary'] = {
                    'type': 'Polygon',
                    'coordinates': [coords]
                }
                project_doc['land']['map'] = project_doc['boundary']
            except Exception:
                flash('Invalid boundary coordinates / إحداثيات الحدود غير صالحة', 'danger')

        result = mongo.db.projects.insert_one(project_doc)
        project_id = project_doc['id']

        flash('Project created successfully / تم إنشاء المشروع بنجاح', 'success')
        return redirect(url_for('project.detail', project_id=project_id))

    project_admins = _project_admins()
    return render_template('project/create.html',
                           project_admins=project_admins,
                           selected_admin_id=_default_admin_id(project_admins))


@project_bp.route('/<int:project_id>')
@login_required
def detail(project_id):
    project = require_project(project_id)
    if not project:
        return redirect(url_for('project.index'))

    project['_id'] = str(project['_id'])
    spec = _get_spec(project)

    # Get geometry elements for the map
    sectors = list(mongo.db.sectors.find({'project_id': project_id}))
    zones = list(mongo.db.zones.find({'project_id': project_id}))
    network = list(mongo.db.network_elements.find({'project_id': project_id}))
    tree_rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    trees = list(mongo.db.trees.find({'project_id': project_id}))

    for s in sectors + zones + network + tree_rows + trees:
        s['_id'] = str(s['_id'])

    project_admin = None
    if project.get('admin_user_id'):
        project_admin = mongo.db.users.find_one({'id': int(project['admin_user_id'])})

    return render_template('project/detail.html',
                           project=project,
                           project_admin=project_admin,
                           project_admins=_project_admins(),
                           spec=spec,
                           sectors=sectors,
                           zones=zones,
                           network=network,
                           tree_rows=tree_rows,
                           trees=trees)


@project_bp.route('/<int:project_id>/settings', methods=['POST'])
@login_required
def settings(project_id):
    project = require_project(project_id)
    if not project:
        return redirect(url_for('project.index'))

    spec = _spec_from_form(request)
    spec['land_documents'] = _land_documents_from_request(
        project_id, (project.get('spec') or {}).get('land_documents') or []
    )

    # Keep the legacy single Point (used by regeneration/hydrology/elevation
    # and KML export) in sync with the first water source that has a position.
    update_fields = {
        'spec': spec,
        'land': _land({**project, 'spec': spec}),
        'water': spec.get('water_sources') or [],
    }
    for w in spec['water_sources']:
        if w.get('lat') is not None and w.get('lng') is not None:
            update_fields['water_source'] = {
                'type': 'Point',
                'coordinates': [w['lng'], w['lat']]
            }
            break
    if 'location' in request.form:
        update_fields['location'] = request.form.get('location', '').strip()
    if 'admin_user_id' in request.form:
        project_admin = _admin_from_form()
        if project_admin:
            update_fields['admin_user_id'] = project_admin['id']
        else:
            flash('Project Admin not updated / لم يتم تحديث مسؤول المشروع', 'warning')

    mongo.db.projects.update_one(
        {'id': project_id},
        {'$set': update_fields}
    )
    flash('Project settings saved / تم حفظ إعدادات المشروع', 'success')
    return redirect(url_for('project.detail', project_id=project_id))


@project_bp.route('/<int:project_id>/land_documents/<int:doc_index>/download')
@login_required
def download_land_document(project_id, doc_index):
    project = require_project(project_id)
    if not project:
        return redirect(url_for('project.index'))

    docs = (project.get('spec') or {}).get('land_documents') or []
    if doc_index < 0 or doc_index >= len(docs):
        flash('Document not found / الوثيقة غير موجودة', 'danger')
        return redirect(url_for('project.detail', project_id=project_id))

    doc = docs[doc_index]
    stored = doc.get('file')
    if not stored:
        flash('No file attached to this document / لا يوجد ملف مرتبط بهذه الوثيقة', 'warning')
        return redirect(url_for('project.detail', project_id=project_id))

    upload_dir = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    filepath = os.path.join(upload_dir, stored)
    if not os.path.isfile(filepath):
        flash('File missing on server / الملف مفقود على الخادم', 'danger')
        return redirect(url_for('project.detail', project_id=project_id))

    return send_file(filepath, as_attachment=True, download_name=doc.get('file_original') or stored)


def _classify_kml_water_source(name):
    """Classify a KML point feature name into a water source type."""
    n = (name or '').strip().lower()
    if re.search(r'\b(well|bore|boring|puit)\b|بئر', n):
        return 'well'
    if re.search(r'\bbasin\b|حوض|أحواض', n):
        return 'basin'
    if re.search(r'\b(reservoir|tank)\b|خزان', n):
        return 'reservoir'
    if re.search(r'\bpump|مضخة|مضخه|مضحات', n):
        return 'pump'
    if re.search(r'\bcanal|قناة|ترعة|قنال|قنوات', n):
        return 'canal'
    if re.search(r'water|source|مياه|مصدر|عين|لاين|طريق|رواد|نهر|بركة', n):
        return 'other'
    return 'other'


def _kml_point_latlng(feature):
    """Return ``(lat, lng)`` of a KML Point / MultiPoint feature."""
    geom_type = feature['geometry'].get('type')
    try:
        if geom_type == 'Point':
            lng, lat = feature['geometry']['coordinates'][:2]
            return float(lat), float(lng)
        if geom_type == 'MultiPoint':
            for pt in feature['geometry'].get('coordinates') or []:
                if pt:
                    lng, lat = pt[:2]
                    return float(lat), float(lng)
    except (TypeError, ValueError, IndexError):
        pass
    return None, None


def _store_kml_features(project_id, project, filename, features):
    from shapely.geometry import shape as shp_shape

    polygon_features = [
        feature for feature in features
        if feature['geometry'].get('type') in ('Polygon', 'MultiPolygon')
    ]
    point_features = [
        feature for feature in features
        if feature['geometry'].get('type') in ('Point', 'MultiPoint')
    ]
    boundary_feature = max(
        polygon_features,
        key=lambda feature: shp_shape(feature['geometry']).area,
        default=None
    )
    sector_features = [
        feature for feature in polygon_features
        if feature is not boundary_feature
        and re.match(r'^(?:s\s*\d+|sector\s*\d*)$', feature['name'].strip(), re.IGNORECASE)
    ]

    for feature in features:
        if feature is boundary_feature:
            feature['category'] = 'boundary'
        elif feature in sector_features:
            feature['category'] = 'sector'
        elif feature in point_features:
            feature['category'] = 'water_source'
        else:
            feature['category'] = 'overlay'

    mongo.db.sectors.delete_many({'project_id': project_id, 'source': 'kml'})
    for feature in sector_features:
        mongo.db.sectors.insert_one({
            'id': get_next_id('sectors'),
            'project_id': project_id,
            'name_en': feature['name'],
            'name_ar': '',
            'polygon': feature['geometry'],
            'validated': False,
            'source': 'kml'
        })

    # Every point feature becomes a water source entry in the project spec.
    water_entries = []
    for feature in point_features:
        lat, lng = _kml_point_latlng(feature)
        if lat is None:
            continue
        water_entries.append({
            'type': _classify_kml_water_source(feature['name']),
            'name': feature['name'] or '',
            'lat': lat,
            'lng': lng,
            'flow_lpm': None,
            'notes': f'Imported from {filename}',
        })

    # Merge KML water sources into the spec, deduped by name + position.
    spec = _get_spec(project)
    existing = list(spec.get('water_sources') or [])

    def _ws_key(w):
        return (
            str(w.get('name') or '').strip().lower(),
            round(float(w.get('lat') or 0), 6),
            round(float(w.get('lng') or 0), 6),
        )

    seen = {_ws_key(w) for w in existing}
    for entry in water_entries:
        key = _ws_key(entry)
        if key in seen:
            continue
        existing.append(entry)
        seen.add(key)
    spec['water_sources'] = existing

    update = {
        'kml_file': filename,
        'kml_features': features,
        'spec': spec,
        'water': spec.get('water_sources') or [],
    }
    if boundary_feature:
        update['boundary'] = boundary_feature['geometry']
    if water_entries:
        first = water_entries[0]
        update['water_source'] = {
            'type': 'Point',
            'coordinates': [first['lng'], first['lat']]
        }
    update['land'] = _land({**project, **update})
    mongo.db.projects.update_one(
        {'id': project_id, 'user_id': project['user_id']},
        {'$set': update}
    )
    return boundary_feature is not None


@project_bp.route('/<int:project_id>/upload_kml', methods=['POST'])
@login_required
def upload_kml(project_id):
    project = require_project(project_id)
    if not project:
        return redirect(url_for('project.index'))

    if 'kml_file' not in request.files:
        flash('No file uploaded / لم يتم رفع ملف', 'danger')
        return redirect(url_for('project.detail', project_id=project_id))

    file = request.files['kml_file']
    if file.filename == '':
        flash('No file selected / لم يتم اختيار ملف', 'danger')
        return redirect(url_for('project.detail', project_id=project_id))

    filename = secure_filename(file.filename)
    upload_dir = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, f'project_{project_id}_{filename}')
    file.save(filepath)

    features = []
    if filename.lower().endswith('.kmz'):
        features = parse_kmz_file(filepath)
    elif filename.lower().endswith('.kml'):
        with open(filepath, 'r', encoding='utf-8') as f:
            features = parse_kml_content(f.read())

    if features:
        has_boundary = _store_kml_features(project_id, project, filename, features)
        if has_boundary:
            flash(f'Loaded {len(features)} features from KML / تم تحميل {len(features)} عنصر', 'success')
        else:
            flash(f'Loaded {len(features)} KML features; no polygon boundary was found / تم تحميل {len(features)} عنصر دون حدود مضلع', 'warning')
    else:
        flash('Could not parse KML file / تعذر تحليل ملف KML', 'danger')

    return redirect(url_for('project.detail', project_id=project_id))


@project_bp.route('/<int:project_id>/export_kml')
@login_required
def export_kml(project_id):
    project = require_project(project_id)
    if not project:
        return redirect(url_for('project.index'))

    kml_content = export_project_to_kml(project_id)
    if not kml_content:
        flash('No geometry to export / لا توجد هندسة للتصدير', 'warning')
        return redirect(url_for('project.detail', project_id=project_id))

    import io
    return send_file(
        io.BytesIO(kml_content.encode('utf-8')),
        as_attachment=True,
        download_name=f'project_{project_id}.kml',
        mimetype='application/vnd.google-earth.kml+xml'
    )


@project_bp.route('/<int:project_id>/delete', methods=['POST'])
@login_required
def delete(project_id):
    project = find_project(project_id)
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return redirect(url_for('project.index'))
    mongo.db.projects.delete_one({'id': project_id})
    try:
        mongo.db.discard(project_id)
    except AttributeError:
        pass
    flash('Project deleted / تم حذف المشروع', 'info')
    return redirect(url_for('project.index'))
