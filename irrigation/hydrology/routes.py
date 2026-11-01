from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.models import get_next_id
from irrigation.engineering.boundaries import adjust_pipes_to_boundaries
from irrigation.engineering.piping import (
    generate_driplines, generate_piping_structure)
from irrigation.engineering.hydraulics import (
    calculate_pipe_length_m, dripline_rules, main_pipe_rules, second_pipe_rules,
    zone_pipe_rules)
from irrigation.engineering.projection import get_project_centroid
from shapely.geometry import shape as geo_shape
import json

hydrology_bp = Blueprint('hydrology', __name__)


def get_project_or_redirect(project_id):
    from irrigation.access import require_project

    project = require_project(project_id)
    if not project:
        return None
    project['_id'] = str(project['_id'])
    return project


def add_network_element(project_id, elem_type, name_en, name_ar, geometry, properties=None):
    elem = {
        'id': get_next_id('network_elements'),
        'project_id': project_id,
        'type': elem_type,
        'name_en': name_en,
        'name_ar': name_ar,
        'geometry': geometry,
        'properties': properties or {},
        'validated': False
    }
    mongo.db.network_elements.insert_one(elem)
    return elem


def _invalidate_piping_report(project_id):
    """Drop the cached AI piping report after a manual change to the network.

    The report is a snapshot of the last AI generation. Once the user adds,
    edits, or removes pipes/valves it no longer matches the stored network,
    so keeping it would show stale counts and lengths (e.g. an "MP" report on
    a project that has no main pipes).
    """
    mongo.db.piping_reports.delete_many({'project_id': project_id})


# === PIPE SECTOR / ZONE COLUMNS ===

def _pipe_shape(geometry):
    """Shapely shape for a pipe path, or None when it cannot be read."""
    if not geometry:
        return None
    try:
        geom = geo_shape(geometry)
    except Exception:
        return None
    return None if geom.is_empty else geom


def _crossed_docs(path, docs, shapes):
    """Sector/zone docs the pipe path traverses, ordered by id.

    A pipe drawn outside every outline still gets an answer: the nearest one.
    """
    hits = [doc for doc_id, doc in docs.items()
            if doc_id in shapes and path.intersects(shapes[doc_id])]
    if hits:
        return sorted(hits, key=lambda d: d.get('id', 0))
    candidates = [doc for doc_id, doc in docs.items() if doc_id in shapes]
    if not candidates:
        return []
    return [min(candidates, key=lambda d: shapes[d['id']].distance(path))]


def _location_label(docs, field, sep=', ', limit=3):
    """Join the names of a few docs; a long list is shortened to ``+N``."""
    names = [doc.get(field) for doc in docs if doc.get(field)]
    if len(names) > limit:
        return sep.join(names[:limit]) + ' +%d' % (len(names) - limit)
    return sep.join(names)


def _attach_pipe_locations(project_id, pipes):
    """Fill ``pipe['location']`` with the sector and zone of every pipe row.

    Generated pipes record the zone (and sector) they were built for in their
    properties, so the table reads the names straight from those ids.  A pipe
    drawn by hand, and the Main Pipe that serves several sectors, carries no
    such link: those fall back to the outlines the pipe path crosses, so the
    columns still show where the pipe runs.
    """
    sectors = {s['id']: s for s in mongo.db.sectors.find({'project_id': project_id})}
    zones = {z['id']: z for z in mongo.db.zones.find({'project_id': project_id})}
    # Drip lines are built per tree row, not per zone, so their zone is read
    # back through the row they serve.
    rows = {r['id']: r for r in mongo.db.tree_rows.find({'project_id': project_id})}


    sector_shapes = {}
    for sector in sectors.values():
        shape_ = _pipe_shape(sector.get('polygon'))
        if shape_ is not None:
            sector_shapes[sector['id']] = shape_

    zone_shapes = {}
    for zone in zones.values():
        shape_ = _pipe_shape(zone.get('polygon'))
        if shape_ is not None:
            zone_shapes[zone['id']] = shape_

    for pipe in pipes:
        props = pipe.get('properties') or {}
        zone_docs = []
        sector_docs = []

        zone_id = props.get('zone_id')
        if zone_id is None and props.get('row_id') is not None:
            zone_id = (rows.get(props.get('row_id')) or {}).get('zone_id')
        zone = zones.get(zone_id)
        if zone:
            zone_docs.append(zone)
        sector = sectors.get(props.get('sector_id')) or sectors.get((zone or {}).get('sector_id'))
        if sector:
            sector_docs.append(sector)

        if not zone_docs or not sector_docs:
            path = _pipe_shape(pipe.get('geometry'))
            if path is not None:
                if not sector_docs:
                    sector_docs = _crossed_docs(path, sectors, sector_shapes)
                if not zone_docs:
                    zone_docs = _crossed_docs(path, zones, zone_shapes)

        pipe['location'] = {
            'sector_en': _location_label(sector_docs, 'name_en'),
            'sector_ar': _location_label(sector_docs, 'name_ar', sep='، '),
            'zone_en': _location_label(zone_docs, 'name_en'),
            'zone_ar': _location_label(zone_docs, 'name_ar', sep='، '),
        }


# === MAIN PIPE ===

@hydrology_bp.route('/<int:project_id>/main_pipe')
@login_required
def main_pipe(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    sectors = list(mongo.db.sectors.find({'project_id': project_id}))
    for s in sectors:
        s['_id'] = str(s['_id'])

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    for z in zones:
        z['_id'] = str(z['_id'])

    main_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'main_pipe'}))
    for p in main_pipes:
        p['_id'] = str(p['_id'])
    _attach_pipe_locations(project_id, main_pipes)

    water_source = project.get('water_source')

    piping_report = mongo.db.piping_reports.find_one({'project_id': project_id})

    return render_template('hydrology/main_pipe.html', project=project, sectors=sectors, zones=zones, main_pipes=main_pipes, water_source=water_source, piping_report=piping_report, pipe_rules=main_pipe_rules())


@hydrology_bp.route('/<int:project_id>/main_pipe/add', methods=['POST'])
@login_required
def main_pipe_add(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    coords = request.form.get('coordinates', '').strip()
    diameter = request.form.get('diameter', default=110, type=int)

    if not coords:
        flash('Pipe path required / مسار الأنبوب مطلوب', 'danger')
        return redirect(url_for('hydrology.main_pipe', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.main_pipe', project_id=project_id))

    geometry = {'type': 'LineString', 'coordinates': coords_list}

    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': 'main_pipe'})
    props = {'diameter': diameter}
    props.update(_pipe_length_props(geometry, project))
    add_network_element(project_id, 'main_pipe',
                        f'Main Pipe {count+1}', f'الأنبوب الرئيسي {count+1}',
                        geometry, props)

    _invalidate_piping_report(project_id)
    flash('Main pipe added / تمت إضافة الأنبوب الرئيسي', 'success')
    return redirect(url_for('hydrology.main_pipe', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/main_pipe/validate', methods=['POST'])
@login_required
def main_pipe_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': 'main_pipe'},
        {'$set': {'validated': True}}
    )
    flash('Main pipe validated / تم التحقق من الأنبوب الرئيسي', 'success')
    return redirect(url_for('hydrology.second_pipe', project_id=project_id))


# === SECOND PIPE ===

@hydrology_bp.route('/<int:project_id>/second_pipe')
@login_required
def second_pipe(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    for z in zones:
        z['_id'] = str(z['_id'])

    second_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'second_pipe'}))
    for p in second_pipes:
        p['_id'] = str(p['_id'])
    _attach_pipe_locations(project_id, second_pipes)

    piping_report = mongo.db.piping_reports.find_one({'project_id': project_id})

    return render_template('hydrology/second_pipe.html', project=project, zones=zones,
                           second_pipes=second_pipes, piping_report=piping_report,
                           pipe_rules=second_pipe_rules())


@hydrology_bp.route('/<int:project_id>/second_pipe/add', methods=['POST'])
@login_required
def second_pipe_add(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    coords = request.form.get('coordinates', '').strip()
    diameter = request.form.get('diameter', default=90, type=int)

    if not coords:
        flash('Pipe path required / مسار الأنبوب مطلوب', 'danger')
        return redirect(url_for('hydrology.second_pipe', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.second_pipe', project_id=project_id))

    geometry = {'type': 'LineString', 'coordinates': coords_list}
    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': 'second_pipe'})

    props = {'diameter': diameter}
    props.update(_pipe_length_props(geometry, project))
    add_network_element(project_id, 'second_pipe',
                        f'Second Pipe {count+1}', f'الأنبوب الثانوي {count+1}',
                        geometry, props)

    _invalidate_piping_report(project_id)
    flash('Second pipe added / تمت إضافة الأنبوب الثانوي', 'success')
    return redirect(url_for('hydrology.second_pipe', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/second_pipe/validate', methods=['POST'])
@login_required
def second_pipe_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': 'second_pipe'},
        {'$set': {'validated': True}}
    )
    flash('Second pipes validated / تم التحقق من الأنابيب الثانوية', 'success')
    return redirect(url_for('hydrology.sub_pipe', project_id=project_id))


# === AI PIPING STRUCTURE GENERATOR ===

def _truthy(value):
    return str(value).lower() in ('1', 'on', 'true', 'yes')


def _pipe_length_props(geometry, project):
    """Best-effort pipe length in meters for a manually added element."""
    try:
        lng, lat = get_project_centroid(project)
        return {'length_m': round(calculate_pipe_length_m(geometry, lng, lat), 2)}
    except Exception:
        return {}


@hydrology_bp.route('/<int:project_id>/piping/generate', methods=['POST'])
@login_required
def piping_generate(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    scope = request.form.get('scope', 'full')
    if scope not in ('full', 'main', 'second', 'zone'):
        scope = 'full'

    options = {
        'scope': scope,
        'use_elevation': _truthy(request.form.get('use_elevation')),
        'replace': _truthy(request.form.get('replace')),
        'mode': request.form.get('mode', 'direct'),
        'source_pressure_bar': request.form.get('source_pressure_bar', type=float),
        'source_flow_lpm': request.form.get('source_flow_lpm', type=float),
    }

    dest = {'zone': 'hydrology.sub_pipe',
            'second': 'hydrology.second_pipe'}.get(scope, 'hydrology.main_pipe')
    try:
        report = generate_piping_structure(project_id, project, options)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for(dest, project_id=project_id))
    except Exception as exc:
        flash('Piping generation failed: %s / فشل إنشاء هيكل الأنابيب' % exc, 'danger')
        return redirect(url_for(dest, project_id=project_id))

    c = report.get('counts', {})
    if scope == 'main':
        flash('Main pipe generated: %d main pipe(s) / تم إنشاء الأنبوب الرئيسي'
              % c.get('main_pipe', 0), 'success')
    elif scope == 'second':
        flash('Second pipes generated: %d second pipe(s) / '
              'تم إنشاء الأنابيب الثانوية' % c.get('second_pipe', 0), 'success')
    elif scope == 'zone':
        flash('Zone pipes generated: %d zone pipe(s), %d drip line(s) / '
              'تم إنشاء أنابيب المناطق' % (c.get('zone_pipe', 0),
                                          c.get('dripline', 0)), 'success')
    else:
        flash(
            'Piping structure generated: %d main pipe(s), %d second pipe(s), '
            '%d zone pipe(s), %d drip line(s) / تم إنشاء هيكل الأنابيب'
            % (c.get('main_pipe', 0), c.get('second_pipe', 0),
               c.get('zone_pipe', 0), c.get('dripline', 0)),
            'success')
    return redirect(url_for(dest, project_id=project_id))


# === SUB PIPE ===

@hydrology_bp.route('/<int:project_id>/sub_pipe')
@login_required
def sub_pipe(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    for z in zones:
        z['_id'] = str(z['_id'])

    sub_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'sub_pipe'}))
    for p in sub_pipes:
        p['_id'] = str(p['_id'])
    _attach_pipe_locations(project_id, sub_pipes)

    piping_report = mongo.db.piping_reports.find_one({'project_id': project_id})

    return render_template('hydrology/sub_pipe.html', project=project, zones=zones, sub_pipes=sub_pipes, piping_report=piping_report, pipe_rules=zone_pipe_rules())


@hydrology_bp.route('/<int:project_id>/sub_pipe/add', methods=['POST'])
@login_required
def sub_pipe_add(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    coords = request.form.get('coordinates', '').strip()
    diameter = request.form.get('diameter', default=63, type=int)
    zone_id = request.form.get('zone_id', type=int)

    if not coords:
        flash('Pipe path required / مسار الأنبوب مطلوب', 'danger')
        return redirect(url_for('hydrology.sub_pipe', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.sub_pipe', project_id=project_id))

    geometry = {'type': 'LineString', 'coordinates': coords_list}
    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': 'sub_pipe'})

    props = {'diameter': diameter, 'zone_id': zone_id}
    props.update(_pipe_length_props(geometry, project))
    add_network_element(project_id, 'sub_pipe',
                        f'Zone Pipe {count+1}', f'أنبوب المنطقة {count+1}',
                        geometry, props)

    _invalidate_piping_report(project_id)
    flash('Zone pipe added / تمت إضافة أنبوب المنطقة', 'success')
    return redirect(url_for('hydrology.sub_pipe', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/sub_pipe/validate', methods=['POST'])
@login_required
def sub_pipe_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': 'sub_pipe'},
        {'$set': {'validated': True}}
    )
    flash('Zone pipes validated / تم التحقق من أنابيب المناطق', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


# === SHARED PIPE ACTIONS (MAIN PIPE & ZONE PIPE) ===

PIPE_TYPES = ('main_pipe', 'second_pipe', 'sub_pipe', 'dripline')


def _pipe_page_endpoint(pipe_type):
    return {
        'main_pipe': 'hydrology.main_pipe',
        'second_pipe': 'hydrology.second_pipe',
        'sub_pipe': 'hydrology.sub_pipe',
        'dripline': 'hydrology.dripline',
    }.get(pipe_type, 'hydrology.main_pipe')


@hydrology_bp.route('/<int:project_id>/pipe/<pipe_type>/rename', methods=['POST'])
@login_required
def pipe_rename(project_id, pipe_type):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    if pipe_type not in PIPE_TYPES:
        flash('Invalid pipe type / نوع أنبوب غير صالح', 'danger')
        return redirect(url_for('hydrology.main_pipe', project_id=project_id))

    pipe_id = request.form.get('pipe_id', type=int)
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()

    updates = {}
    if name_en:
        updates['name_en'] = name_en
    if name_ar:
        updates['name_ar'] = name_ar

    if pipe_id and updates:
        result = mongo.db.network_elements.update_one(
            {'id': pipe_id, 'project_id': project_id, 'type': pipe_type},
            {'$set': updates})
        if result.matched_count:
            flash('Pipe renamed / تمت إعادة تسمية الأنبوب', 'success')
        else:
            flash('Pipe not found / الأنبوب غير موجود', 'warning')

    return redirect(url_for(_pipe_page_endpoint(pipe_type), project_id=project_id))


@hydrology_bp.route('/<int:project_id>/pipe/<pipe_type>/edit', methods=['POST'])
@login_required
def pipe_update(project_id, pipe_type):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    if pipe_type not in PIPE_TYPES:
        flash('Invalid pipe type / نوع أنبوب غير صالح', 'danger')
        return redirect(url_for('hydrology.main_pipe', project_id=project_id))

    pipe_id = request.form.get('pipe_id', type=int)
    diameter = request.form.get('diameter', type=int)
    coords = request.form.get('coordinates', '').strip()

    updates = {}
    if diameter:
        updates['properties.diameter'] = diameter
    if coords:
        try:
            coords_list = json.loads(coords)
        except Exception:
            flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
            return redirect(url_for(_pipe_page_endpoint(pipe_type), project_id=project_id))
        geometry = {'type': 'LineString', 'coordinates': coords_list}
        updates['geometry'] = geometry
        length_props = _pipe_length_props(geometry, project)
        if 'length_m' in length_props:
            updates['properties.length_m'] = length_props['length_m']

    if pipe_id and updates:
        result = mongo.db.network_elements.update_one(
            {'id': pipe_id, 'project_id': project_id, 'type': pipe_type},
            {'$set': updates})
        if result.matched_count:
            flash('Pipe updated / تم تحديث الأنبوب', 'success')
            _invalidate_piping_report(project_id)
        else:
            flash('Pipe not found / الأنبوب غير موجود', 'warning')
    else:
        flash('Nothing to update / لا يوجد شيء للتحديث', 'warning')

    return redirect(url_for(_pipe_page_endpoint(pipe_type), project_id=project_id))


@hydrology_bp.route('/<int:project_id>/pipe/<pipe_type>/remove', methods=['POST'])
@login_required
def pipe_remove(project_id, pipe_type):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    if pipe_type not in PIPE_TYPES:
        flash('Invalid pipe type / نوع أنبوب غير صالح', 'danger')
        return redirect(url_for('hydrology.main_pipe', project_id=project_id))

    pipe_ids = [int(v) for v in request.form.getlist('pipe_ids') if str(v).strip().isdigit()]
    if not pipe_ids:
        flash('Select at least one pipe first / اختر أنبوبا واحدا على الأقل أولا', 'warning')
        return redirect(url_for(_pipe_page_endpoint(pipe_type), project_id=project_id))

    result = mongo.db.network_elements.delete_many(
        {'project_id': project_id, 'type': pipe_type, 'id': {'$in': pipe_ids}})
    if result.deleted_count:
        _invalidate_piping_report(project_id)
    flash('Removed %d pipe(s) / تمت إزالة %d أنبوب' % (result.deleted_count, result.deleted_count), 'success')
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True, 'removed': result.deleted_count, 'ids': pipe_ids})
    return redirect(url_for(_pipe_page_endpoint(pipe_type), project_id=project_id))


@hydrology_bp.route('/<int:project_id>/pipe/<pipe_type>/adjust-boundaries', methods=['POST'])
@login_required
def pipe_adjust_boundaries(project_id, pipe_type):
    """Route every pipe of one level along the land / sector / zone boundaries.

    Every pipe of the page is adjusted at once (Pipes -> Adjust) and its two
    ends stay where they are, so the network keeps its connections while the
    path in between follows the boundary lines instead of cutting through the
    blocks (see ``irrigation.engineering.boundaries``).
    """
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    if pipe_type not in ('main_pipe', 'second_pipe', 'sub_pipe'):
        flash('Invalid pipe type / نوع أنبوب غير صالح', 'danger')
        return redirect(url_for('hydrology.main_pipe', project_id=project_id))

    try:
        report = adjust_pipes_to_boundaries(project_id, project, pipe_type)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for(_pipe_page_endpoint(pipe_type), project_id=project_id))
    except Exception as exc:
        flash('Boundary adjustment failed: %s / فشل ضبط الأنابيب على الحدود' % exc,
              'danger')
        return redirect(url_for(_pipe_page_endpoint(pipe_type), project_id=project_id))

    scope_en = ('the land, sector and zone boundaries'
                if pipe_type == 'sub_pipe' else 'the land and sector boundaries')
    scope_ar = ('حدود الأرض والقطاعات والمناطق'
                if pipe_type == 'sub_pipe' else 'حدود الأرض والقطاعات')

    if not report['total']:
        flash('No pipes to adjust yet / لا توجد أنابيب لضبطها بعد', 'warning')
    elif report['adjusted']:
        _invalidate_piping_report(project_id)
        flash(
            'Adjusted %d pipe(s) to %s: length %.1f m -> %.1f m / '
            'تم ضبط %d أنبوب على %s: الطول %.1f م -> %.1f م'
            % (report['adjusted'], scope_en, report['length_before_m'],
               report['length_after_m'], report['adjusted'], scope_ar,
               report['length_before_m'], report['length_after_m']),
            'success')
    elif report['skipped']:
        flash('%d pipe(s) could not be adjusted / تعذر ضبط %d أنبوب'
              % (report['skipped'], report['skipped']), 'warning')
    else:
        flash('Every pipe already follows the boundaries / '
              'كل الأنابيب تتبع الحدود بالفعل', 'info')
    return redirect(url_for(_pipe_page_endpoint(pipe_type), project_id=project_id))


# === DRIP LINE ===

# Rule DL-1: every tree row is served by its own drip line. The stage therefore
# opens only once the rows have been validated — the tab stays disabled until
# then, and a direct link bounces back to the Rows tab.

def _dripline_rows_guard(project_id):
    from irrigation.access import rows_validated

    if rows_validated(project_id):
        return None
    flash('Validate the tree rows first to open the drip line stage / '
          'يجب التحقق من صفوف الأشجار أولا لفتح خطوط التنقيط', 'warning')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/dripline')
@login_required
def dripline(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    blocked = _dripline_rows_guard(project_id)
    if blocked:
        return blocked

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    for z in zones:
        z['_id'] = str(z['_id'])

    tree_rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    for r in tree_rows:
        r['_id'] = str(r['_id'])
    rows = {r['id']: r for r in tree_rows}

    driplines = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'dripline'}))
    for d in driplines:
        d['_id'] = str(d['_id'])
        d['row'] = rows.get((d.get('properties') or {}).get('row_id'))
    _attach_pipe_locations(project_id, driplines)

    piping_report = mongo.db.piping_reports.find_one({'project_id': project_id})

    return render_template('hydrology/dripline.html', project=project, zones=zones,
                           tree_rows=tree_rows, driplines=driplines,
                           piping_report=piping_report,
                           pipe_rules=dripline_rules())


@hydrology_bp.route('/<int:project_id>/dripline/add', methods=['POST'])
@login_required
def dripline_add(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    blocked = _dripline_rows_guard(project_id)
    if blocked:
        return blocked

    coords = request.form.get('coordinates', '').strip()
    diameter = request.form.get('diameter', default=16, type=int)
    row_id = request.form.get('row_id', type=int)
    zone_id = request.form.get('zone_id', type=int)

    if not coords:
        flash('Dripline path required / مسار خط التنقيط مطلوب', 'danger')
        return redirect(url_for('hydrology.dripline', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.dripline', project_id=project_id))

    geometry = {'type': 'LineString', 'coordinates': coords_list}
    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': 'dripline'})

    props = {'diameter': diameter, 'row_id': row_id, 'zone_id': zone_id}
    props.update(_pipe_length_props(geometry, project))
    add_network_element(project_id, 'dripline',
                        f'Drip Line {count+1}', f'خط التنقيط {count+1}',
                        geometry, props)

    flash('Dripline added / تمت إضافة خط التنقيط', 'success')
    return redirect(url_for('hydrology.dripline', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/dripline/generate', methods=['POST'])
@login_required
def dripline_generate(project_id):
    """Auto Generate: one drip line per tree row (Rule DL-1)."""
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    blocked = _dripline_rows_guard(project_id)
    if blocked:
        return blocked

    try:
        report = generate_driplines(project_id, project,
                                    {'replace': _truthy(request.form.get('replace'))})
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('hydrology.dripline', project_id=project_id))
    except Exception as exc:
        flash('Drip line generation failed: %s / فشل إنشاء خطوط التنقيط' % exc, 'danger')
        return redirect(url_for('hydrology.dripline', project_id=project_id))

    counts = report.get('counts', {})
    message = ('Drip lines generated: %d, one per row / تم إنشاء خطوط التنقيط: %d، خط لكل صف'
               % (counts.get('dripline', 0), counts.get('dripline', 0)))
    if counts.get('skipped'):
        message += (' — %d row(s) skipped (they already have their own drip line '
                    'or no path) / %d صف تم تجاوزه (لديه خط تنقيط أو لا مسار له)'
                    % (counts['skipped'], counts['skipped']))
    flash(message, 'success')
    return redirect(url_for('hydrology.dripline', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/dripline/validate', methods=['POST'])
@login_required
def dripline_validate(project_id):
    blocked = _dripline_rows_guard(project_id)
    if blocked:
        return blocked
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': 'dripline'},
        {'$set': {'validated': True}}
    )
    flash('Driplines validated / تم التحقق من خطوط التنقيط', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


# === NETWORK ELEMENTS ===

@hydrology_bp.route('/<int:project_id>/network')
@login_required
def network_elements(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    elem_types = ['check_valve', 'air_release', 'pressure_reducer', 'reduction', 'other']
    elements = {}
    for t in elem_types:
        elements[t] = list(mongo.db.network_elements.find({'project_id': project_id, 'type': t}))
        for e in elements[t]:
            e['_id'] = str(e['_id'])

    return render_template('hydrology2/network.html', project=project, elements=elements)


@hydrology_bp.route('/<int:project_id>/network/add', methods=['POST'])
@login_required
def network_add(project_id):
    elem_type = request.form.get('elem_type', '')
    coords = request.form.get('coordinates', '').strip()
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    notes = request.form.get('notes', '').strip()

    if not coords or not elem_type:
        flash('Element type and location required / نوع وموقع العنصر مطلوبان', 'danger')
        return redirect(url_for('hydrology.network_elements', project_id=project_id))

    try:
        coord = json.loads(coords)
        if isinstance(coord, list) and len(coord) == 2:
            geometry = {'type': 'Point', 'coordinates': coord}
        else:
            flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
            return redirect(url_for('hydrology.network_elements', project_id=project_id))
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.network_elements', project_id=project_id))

    type_map = {
        'check_valve': ('Check Valve', 'صمام عدم رجوع'),
        'air_release': ('Air Release', 'تنفيس الهواء'),
        'pressure_reducer': ('Pressure Reducer', 'خافض الضغط'),
        'reduction': ('Reduction', 'تقليل قطر'),
        'other': ('Other Element', 'عنصر آخر'),
    }

    if elem_type not in type_map:
        flash('Invalid element type / نوع عنصر غير صالح', 'danger')
        return redirect(url_for('hydrology.network_elements', project_id=project_id))

    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': elem_type})
    default_en, default_ar = type_map[elem_type]
    add_network_element(project_id, elem_type,
                        name_en or f'{default_en} {count+1}',
                        name_ar or f'{default_ar} {count+1}',
                        geometry, {'notes': notes})

    flash('Network element added / تمت إضافة عنصر الشبكة', 'success')
    return redirect(url_for('hydrology.network_elements', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/network/validate', methods=['POST'])
@login_required
def network_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': {'$in': ['check_valve', 'air_release', 'pressure_reducer', 'reduction', 'other']}},
        {'$set': {'validated': True}}
    )
    mongo.db.projects.update_one(
        {'id': project_id},
        {'$set': {'status': 'complete'}}
    )
    flash('Network validated / تم التحقق من الشبكة', 'success')
    return redirect(url_for('project.detail', project_id=project_id))


def _remove_network_elements(project_id, allowed_types, redirect_endpoint):
    _project = get_project_or_redirect(project_id)
    if not _project:
        return redirect(url_for('project.index'))

    ids = [int(v) for v in request.form.getlist('element_ids') if str(v).strip().lstrip('-').isdigit()]
    if not ids:
        flash('Select at least one element first / اختر عنصرا واحدا على الأقل أولا', 'warning')
        return redirect(url_for(redirect_endpoint, project_id=project_id))

    query = {'project_id': project_id, 'id': {'$in': ids}}
    if allowed_types:
        query['type'] = {'$in': allowed_types}
    result = mongo.db.network_elements.delete_many(query)
    if result.deleted_count:
        _invalidate_piping_report(project_id)
    flash('Removed %d element(s) / تمت إزالة %d عنصر' % (result.deleted_count, result.deleted_count), 'success')
    return redirect(url_for(redirect_endpoint, project_id=project_id))


@hydrology_bp.route('/<int:project_id>/dripline/remove', methods=['POST'])
@login_required
def dripline_remove(project_id):
    return _remove_network_elements(project_id, ['dripline'], 'hydrology.dripline')


@hydrology_bp.route('/<int:project_id>/network/remove', methods=['POST'])
@login_required
def network_remove(project_id):
    return _remove_network_elements(
        project_id,
        ['check_valve', 'air_release', 'pressure_reducer', 'reduction', 'other'],
        'hydrology.network_elements')
