from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.models import get_next_id, assign_short_names
import json
import math
import re

trees_bp = Blueprint('trees', __name__)


def _principal_axis_angle(poly):
    """Angle (radians) of the dominant axis of a projected polygon exterior."""
    xs, ys = poly.exterior.xy
    xs = list(xs)[:-1]
    ys = list(ys)[:-1]
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov_xx = sum((x - mx) ** 2 for x in xs)
    cov_xy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    cov_yy = sum((y - my) ** 2 for y in ys)
    return 0.5 * math.atan2(2 * cov_xy, cov_xx - cov_yy)


def _trace_rows_in_poly(poly, spacing_m, angle_deg=None):
    """Trace parallel rows inside a projected polygon along its dominant axis.

    ``angle_deg`` forces the row direction (degrees) instead of the dominant axis.
    Returns a list of ``(LineString, orientation_deg)`` pairs.
    """
    from shapely.geometry import LineString
    parts = poly.geoms if poly.geom_type == 'MultiPolygon' else [poly]
    forced = None if angle_deg is None else math.radians(angle_deg)
    rows = []
    for part in parts:
        angle = forced if forced is not None else _principal_axis_angle(part)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        origin = part.centroid
        origin_n = origin.x * nx + origin.y * ny
        minx, miny, maxx, maxy = part.bounds
        corners = [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
        tvals = [c[0] * nx + c[1] * ny for c in corners]
        tmin, tmax = min(tvals), max(tvals)
        half = math.hypot(maxx - minx, maxy - miny) * 2.0
        t = tmin
        while t <= tmax + 1e-9:
            offset = t - origin_n
            line = LineString([
                (origin.x + offset * nx - half * ux, origin.y + offset * ny - half * uy),
                (origin.x + offset * nx + half * ux, origin.y + offset * ny + half * uy),
            ])
            inter = part.intersection(line)
            segs = inter.geoms if inter.geom_type in ('MultiLineString', 'GeometryCollection') else [inter]
            for seg in segs:
                if seg.geom_type == 'LineString' and seg.length >= max(spacing_m * 0.5, 1.0):
                    rows.append((seg, math.degrees(angle) % 180))
            t += spacing_m
    return rows


def _zone_bearing_deg(zone):
    """True bearing (0-360) the zone's rows must follow, or ``None`` for auto."""
    from irrigation.engineering.elevation import ORIENTATION_DEGREES
    degrees = zone.get('orientation_deg')
    if degrees is None:
        degrees = ORIENTATION_DEGREES.get(zone.get('orientation'))
    try:
        return None if degrees is None else float(degrees) % 360.0
    except (TypeError, ValueError):
        return None


def _row_angle_for_bearing(bearing_deg, lng, lat):
    """Projected-plane angle that traces rows along ``bearing_deg``.

    Zones are cut so their strips run along their orientation bearing, and the
    rows of a zone must run the same way. On the UTM plane a line at true
    bearing B is ``B - convergence`` clockwise from north, i.e.
    ``90 - (B - convergence)`` counter-clockwise from +x -- the angle
    ``_trace_rows_in_poly`` expects.
    """
    from irrigation.engineering.projection import meridian_convergence
    grid_bearing = (float(bearing_deg) - meridian_convergence(lng, lat)) % 180.0
    return (90.0 - grid_bearing) % 180.0


def _apply_zone_orientation(project_id, zone_id, bearing_deg):
    """Store ``bearing_deg`` on the zone so rows and zone cannot disagree."""
    from irrigation.engineering.elevation import compass_from_bearing
    degrees = round(float(bearing_deg) % 360.0, 1)
    mongo.db.zones.update_one(
        {'id': zone_id, 'project_id': project_id},
        {'$set': {'orientation': compass_from_bearing(degrees),
                  'orientation_deg': degrees}}
    )


def get_project_or_redirect(project_id):
    from irrigation.access import require_project

    project = require_project(project_id)
    if not project:
        return None
    project['_id'] = str(project['_id'])
    return project


# === ROWS ===

_SHORT_NAME_PARTS = re.compile(r'(\d+)')


def _short_name_key(row):
    """Natural sort key so ``S1Z1R2`` sorts before ``S1Z1R10``."""
    parts = []
    for chunk in _SHORT_NAME_PARTS.split(row.get('short_name') or ''):
        if not chunk:
            continue
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk.lower()))
    return (parts, row.get('id') or 0)


@trees_bp.route('/<int:project_id>/rows')
@login_required
def rows_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    assign_short_names(project_id)

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    for z in zones:
        z['_id'] = str(z['_id'])
    zone_by_id = {z['id']: z for z in zones}

    rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    rows.sort(key=_short_name_key)

    rows_per_zone = {}
    for r in rows:
        rows_per_zone[r.get('zone_id')] = rows_per_zone.get(r.get('zone_id'), 0) + 1

    zone_options = []
    bearing_by_zone = {}
    for z in zones:
        bearing = _zone_bearing_deg(z)
        bearing_by_zone[z.get('id')] = bearing
        zone_options.append({
            'id': z.get('id'),
            'code': z.get('short_name') or '',
            'en': z.get('name_en') or '',
            'ar': z.get('name_ar') or '',
            'orientation': z.get('orientation'),
            'orientation_deg': bearing,
            'rows': rows_per_zone.get(z.get('id'), 0),
        })
    zone_options.sort(key=lambda z: _short_name_key({'short_name': z['code'], 'id': z['id']}))

    for r in rows:
        r['_id'] = str(r['_id'])
        zz = zone_by_id.get(r.get('zone_id'))
        r['zone_name_en'] = zz['name_en'] if zz else f"Zone {r.get('zone_id')}"
        r['zone_name_ar'] = zz['name_ar'] if zz else ''
        r['zone_orientation_deg'] = bearing_by_zone.get(r.get('zone_id'))

    return render_template('trees/rows.html', project=project, rows=rows, zone_options=zone_options)


@trees_bp.route('/<int:project_id>/rows/add', methods=['POST'])
@login_required
def row_add(project_id):
    zone_id = request.form.get('zone_id', type=int)
    coords = request.form.get('coordinates', '').strip()
    spacing = request.form.get('spacing', default=5, type=float)
    orientation = request.form.get('orientation', default=0, type=float)

    if not zone_id or not coords:
        flash('Zone and row path required / المنطقة ومسار الصف مطلوبان', 'danger')
        return redirect(url_for('trees.rows_view', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('trees.rows_view', project_id=project_id))

    count = mongo.db.tree_rows.count_documents({'project_id': project_id})
    row = {
        'id': get_next_id('tree_rows'),
        'project_id': project_id,
        'zone_id': zone_id,
        'name_en': f'Row {count+1}',
        'name_ar': f'صف {count+1}',
        'geometry': {'type': 'LineString', 'coordinates': coords_list},
        'spacing_m': spacing,
        'orientation': orientation,
        'validated': False
    }
    mongo.db.tree_rows.insert_one(row)
    flash('Row added / تمت إضافة الصف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/generate', methods=['POST'])
@login_required
def rows_generate(project_id):
    """Trace rows inside every zone at the given distance between rows."""
    spacing = request.form.get('spacing', default=5.0, type=float)
    replace = request.form.get('replace') in ('1', 'on', 'true')
    if spacing < 1:
        flash('Invalid row spacing / مسافة الصفوف غير صالحة', 'danger')
        return redirect(url_for('trees.rows_view', project_id=project_id))

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    if not zones:
        flash('No zones found. Create zones first / لا توجد مناطق. أنشئ المناطق أولاً', 'warning')
        return redirect(url_for('trees.rows_view', project_id=project_id))

    from irrigation.engineering.projection import projected_shape, unproject_shape, get_project_centroid
    project_doc = mongo.db.projects.find_one({'id': project_id})
    lng, lat = get_project_centroid(project_doc) if project_doc else (0.0, 0.0)

    if replace:
        mongo.db.tree_rows.delete_many({'project_id': project_id})

    count = mongo.db.tree_rows.count_documents({'project_id': project_id})
    total = 0
    for z in zones:
        geom = z.get('polygon')
        if not geom:
            continue
        try:
            poly = projected_shape(geom, lng, lat)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            continue
        if poly.is_empty:
            continue
        # Rows follow the zone orientation; only zones with none fall back to
        # their dominant axis (the reported traced angle).
        zone_bearing = _zone_bearing_deg(z)
        angle_deg = (None if zone_bearing is None
                     else _row_angle_for_bearing(zone_bearing, lng, lat))
        for line, traced_angle in _trace_rows_in_poly(poly, spacing, angle_deg=angle_deg):
            count += 1
            row = {
                'id': get_next_id('tree_rows'),
                'project_id': project_id,
                'zone_id': z['id'],
                'name_en': f'Row {count}',
                'name_ar': f'صف {count}',
                'geometry': unproject_shape(line, lng, lat),
                'spacing_m': round(spacing, 2),
                'orientation': round(zone_bearing if zone_bearing is not None
                                     else traced_angle, 1),
                'validated': False,
                'auto_generated': True
            }
            mongo.db.tree_rows.insert_one(row)
            total += 1

    flash(f'Generated {total} rows along the zone orientations'
          f' / تم إنشاء {total} صف وفق اتجاهات المناطق', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/change-orientation', methods=['POST'])
@login_required
def rows_change_orientation(project_id):
    """Rotate the row direction of every zone behind the selected rows."""
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get('row_ids')
    if raw_ids is None:
        raw_ids = request.form.getlist('row_ids')

    raw_angle = payload.get('angle')
    if raw_angle is None:
        raw_angle = request.form.get('angle')
    try:
        forced_angle = None if raw_angle in (None, '') else float(raw_angle) % 180.0
    except (TypeError, ValueError):
        forced_angle = None

    ids = []
    for raw in raw_ids or []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return jsonify({'ok': False, 'message': 'No rows selected / لم يتم تحديد صفوف'}), 400

    selected = list(mongo.db.tree_rows.find({'project_id': project_id, 'id': {'$in': ids}}))
    if not selected:
        return jsonify({'ok': False, 'message': 'Rows not found / الصفوف غير موجودة'}), 404

    selected_by_zone = {}
    for row in selected:
        zone_id = row.get('zone_id')
        if zone_id is not None:
            selected_by_zone.setdefault(zone_id, []).append(row)
    if not selected_by_zone:
        return jsonify({'ok': False, 'message': 'Rows have no zone / الصفوف بدون منطقة'}), 404

    zones = list(mongo.db.zones.find(
        {'project_id': project_id, 'id': {'$in': list(selected_by_zone)}}))

    from irrigation.engineering.projection import (
        projected_shape, unproject_shape, get_project_centroid)
    project_doc = mongo.db.projects.find_one({'id': project_id})
    lng, lat = get_project_centroid(project_doc) if project_doc else (0.0, 0.0)

    changed_zones = 0
    replaced_rows = 0
    removed_trees = 0

    for zone in zones:
        zone_selected = selected_by_zone.get(zone['id']) or []
        # Row orientation is a true bearing (0 = north), same scale as the zone.
        try:
            base_bearing = float(zone_selected[0].get('orientation') or 0.0)
        except (TypeError, ValueError):
            base_bearing = 0.0
        if forced_angle is not None:
            new_bearing = forced_angle % 180.0
        else:
            new_bearing = (base_bearing + 90.0) % 180.0

        poly = None
        geom = zone.get('polygon')
        if geom:
            try:
                poly = projected_shape(geom, lng, lat)
                if not poly.is_valid:
                    poly = poly.buffer(0)
            except Exception:
                poly = None
            if poly is not None and poly.is_empty:
                poly = None

        if poly is None:
            # No geometry to re-trace: keep the DB consistent by rotating the stored angle.
            mongo.db.tree_rows.update_many(
                {'project_id': project_id, 'zone_id': zone['id']},
                {'$set': {'orientation': round(new_bearing, 1)}}
            )
            _apply_zone_orientation(project_id, zone['id'], new_bearing)
            changed_zones += 1
            continue

        zone_row = mongo.db.tree_rows.find_one(
            {'project_id': project_id, 'zone_id': zone['id']})
        spacing = (zone_row or {}).get('spacing_m') or 5.0
        spacing = spacing if spacing >= 1 else 5.0

        traced = _trace_rows_in_poly(
            poly, spacing, angle_deg=_row_angle_for_bearing(new_bearing, lng, lat))

        removed_trees += mongo.db.trees.delete_many(
            {'project_id': project_id, 'zone_id': zone['id']}).deleted_count
        mongo.db.tree_rows.delete_many(
            {'project_id': project_id, 'zone_id': zone['id']})

        count = mongo.db.tree_rows.count_documents({'project_id': project_id})
        for line, _traced_angle in traced:
            count += 1
            mongo.db.tree_rows.insert_one({
                'id': get_next_id('tree_rows'),
                'project_id': project_id,
                'zone_id': zone['id'],
                'name_en': f'Row {count}',
                'name_ar': f'صف {count}',
                'geometry': unproject_shape(line, lng, lat),
                'spacing_m': round(spacing, 2),
                'orientation': round(new_bearing, 1),
                'validated': False,
                'auto_generated': True
            })
            replaced_rows += 1
        _apply_zone_orientation(project_id, zone['id'], new_bearing)
        changed_zones += 1

    if not changed_zones:
        return jsonify({'ok': False, 'message': 'No zones updated / لم يتم تحديث أي منطقة'}), 404

    assign_short_names(project_id)

    return jsonify({
        'ok': True,
        'zones': changed_zones,
        'rows': replaced_rows,
        'trees_removed': removed_trees,
        'angle': round(forced_angle, 1) if forced_angle is not None else None,
        'message': (f'Orientation changed for {changed_zones} zone(s), {replaced_rows} rows re-traced'
                    f' ({removed_trees} trees removed)'
                    f' / تم تغيير اتجاه {changed_zones} منطقة وإعادة رسم {replaced_rows} صف')
    })


@trees_bp.route('/<int:project_id>/rows/<int:row_id>/edit', methods=['POST'])
@login_required
def rows_edit(project_id, row_id):
    update = {}
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    if name_en:
        update['name_en'] = name_en
    if name_ar:
        update['name_ar'] = name_ar
    spacing = request.form.get('spacing', default=0, type=float)
    if spacing >= 1:
        update['spacing_m'] = round(spacing, 2)
    # Orientation is owned by the zone (rows must follow it), so it is not
    # editable per row; use the Change Orientation action instead.
    mongo.db.tree_rows.update_one(
        {'id': row_id, 'project_id': project_id},
        {'$set': update}
    )
    flash('Row updated / تم تحديث الصف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/<int:row_id>/rename', methods=['POST'])
@login_required
def rows_rename(project_id, row_id):
    update = {}
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    if name_en:
        update['name_en'] = name_en
    if name_ar:
        update['name_ar'] = name_ar
    mongo.db.tree_rows.update_one(
        {'id': row_id, 'project_id': project_id},
        {'$set': update}
    )
    flash('Row renamed / تمت إعادة تسمية الصف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/remove', methods=['POST'])
@login_required
def rows_remove(project_id):
    ids = request.form.getlist('row_ids')
    removed = 0
    for raw in ids:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        res = mongo.db.tree_rows.delete_one({'id': rid, 'project_id': project_id})
        removed += res.deleted_count
    flash(f'Removed {removed} rows / تمت إزالة {removed} صف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/validate', methods=['POST'])
@login_required
def rows_validate(project_id):
    mongo.db.tree_rows.update_many(
        {'project_id': project_id},
        {'$set': {'validated': True}}
    )
    flash('Rows validated / تم التحقق من الصفوف', 'success')
    return redirect(url_for('trees.trees_view', project_id=project_id))


# === TREES ===

@trees_bp.route('/<int:project_id>/trees')
@login_required
def trees_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    assign_short_names(project_id)

    rows = list(mongo.db.tree_rows.find({'project_id': project_id}).sort('id', 1))
    for r in rows:
        r['_id'] = str(r['_id'])

    trees = list(mongo.db.trees.find({'project_id': project_id}))
    for t in trees:
        t['_id'] = str(t['_id'])

    sectors = list(mongo.db.sectors.find({'project_id': project_id}).sort('id', 1))
    for s in sectors:
        s['_id'] = str(s['_id'])

    zones = list(mongo.db.zones.find({'project_id': project_id}).sort('id', 1))
    for z in zones:
        z['_id'] = str(z['_id'])

    row_counts = {}
    for r in rows:
        row_counts[r.get('zone_id')] = row_counts.get(r.get('zone_id'), 0) + 1
    for z in zones:
        z['row_count'] = row_counts.get(z['id'], 0)

    # Order trees by Sector -> Zone -> Row (fallback rank keeps unknowns last).
    sector_rank = {s['id']: i for i, s in enumerate(sectors)}
    zones_sorted = sorted(
        zones,
        key=lambda z: (sector_rank.get(z.get('sector_id'), len(sectors)), z['id'])
    )
    zone_rank = {z['id']: i for i, z in enumerate(zones_sorted)}
    row_rank = {r['id']: i for i, r in enumerate(rows)}
    trees.sort(key=lambda t: (
        zone_rank.get(t.get('zone_id'), len(zones)),
        row_rank.get(t.get('row_id'), len(rows)),
        t.get('short_name', ''),
        t['id']
    ))

    # Modal groups: order zones by short name, keeping consecutive sectors together.
    sector_by_id = {s['id']: s for s in sectors}
    zone_groups = []
    for z in sorted(zones, key=_short_name_key):
        if not zone_groups or zone_groups[-1]['sector_id'] != z.get('sector_id'):
            zone_groups.append({
                'sector_id': z.get('sector_id'),
                'sector': sector_by_id.get(z.get('sector_id')),
                'zones': [],
            })
        zone_groups[-1]['zones'].append(z)

    return render_template('trees/trees.html', project=project, rows=rows, trees=trees,
                           sectors=sectors, zones=zones, zone_groups=zone_groups)


@trees_bp.route('/<int:project_id>/trees/add', methods=['POST'])
@login_required
def tree_add(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    zone_id = request.form.get('zone_id', type=int)
    row_id = request.form.get('row_id', type=int)
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    lng = request.form.get('lng', type=float)
    lat = request.form.get('lat', type=float)
    variety_en = request.form.get('variety_en', 'Olive').strip()
    variety_ar = request.form.get('variety_ar', 'زيتون').strip()
    drips_per_tree = request.form.get('drips_per_tree', default=2, type=int)
    spacing_m = request.form.get('spacing_m', default=4, type=float)

    if not zone_id or not row_id:
        flash('Zone and Row required / المنطقة والصف مطلوبان', 'danger')
        return redirect(url_for('trees.trees_view', project_id=project_id))

    geometry = {'type': 'Point', 'coordinates': [lng, lat]} if lng is not None and lat is not None else None

    tree = {
        'id': get_next_id('trees'),
        'project_id': project_id,
        'zone_id': zone_id,
        'row_id': row_id,
        'name_en': name_en or f'T{zone_id}-{row_id}',
        'name_ar': name_ar or f'شجرة {zone_id}-{row_id}',
        'geometry': geometry,
        'variety_en': variety_en,
        'variety_ar': variety_ar,
        'variety_pct': None,
        'spacing_m': spacing_m,
        'drips_per_tree': drips_per_tree,
        'validated': False
    }
    mongo.db.trees.insert_one(tree)
    flash('Tree added / تمت إضافة الشجرة', 'success')
    return redirect(url_for('trees.trees_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/trees/edit', methods=['POST'])
@login_required
def tree_edit(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    tree_id = request.form.get('tree_id', type=int)
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    variety_en = request.form.get('variety_en', '').strip()
    variety_ar = request.form.get('variety_ar', '').strip()
    spacing_m = request.form.get('spacing_m', default=4, type=float)
    drips_per_tree = request.form.get('drips_per_tree', default=2, type=int)

    if not tree_id:
        flash('Tree ID required / معرّف الشجرة مطلوب', 'danger')
        return redirect(url_for('trees.trees_view', project_id=project_id))

    tree = mongo.db.trees.find_one({'id': tree_id, 'project_id': project_id})
    if not tree:
        flash('Tree not found / الشجرة غير موجودة', 'danger')
        return redirect(url_for('trees.trees_view', project_id=project_id))

    update_data = {
        'name_en': name_en or tree.get('name_en'),
        'name_ar': name_ar or tree.get('name_ar'),
        'variety_en': variety_en,
        'variety_ar': variety_ar,
        'spacing_m': spacing_m,
        'drips_per_tree': drips_per_tree
    }
    mongo.db.trees.update_one({'id': tree_id, 'project_id': project_id}, {'$set': update_data})
    flash('Tree updated / تم تحديث الشجرة', 'success')
    return redirect(url_for('trees.trees_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/trees/remove', methods=['POST'])
@login_required
def trees_remove(project_id):
    ids = []
    for raw in request.form.getlist('tree_ids'):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if ids:
        removed = mongo.db.trees.delete_many(
            {'id': {'$in': ids}, 'project_id': project_id}).deleted_count
    else:
        removed = mongo.db.trees.delete_many({'project_id': project_id}).deleted_count
    flash(f'Removed {removed} tree(s) / تمت إزالة {removed} شجرة', 'success')
    return redirect(url_for('trees.trees_view', project_id=project_id))


VARIETY_SLOTS = 3


def _zone_setup_from_form(form, zone_id):
    """Read one zone's setup: spacing, drips, first-tree offset and variety/percentage entries."""
    spacing = form.get(f'spacing_{zone_id}', type=float)
    drips = form.get(f'drips_{zone_id}', type=int)
    offset = form.get(f'offset_{zone_id}', type=float)

    varieties = []
    for slot in range(1, VARIETY_SLOTS + 1):
        en = (form.get(f'variety_{zone_id}_{slot}') or '').strip()
        ar = (form.get(f'variety_ar_{zone_id}_{slot}') or '').strip()
        pct = form.get(f'pct_{zone_id}_{slot}', type=float)
        if not en and not pct:
            continue
        varieties.append({
            'en': en,
            'ar': ar or en,
            'pct': round(pct, 2) if pct and pct > 0 else 0.0
        })

    if not varieties and spacing is None:
        return None

    return {
        'spacing_m': round(spacing, 2) if spacing and spacing >= 1 else None,
        'drips_per_tree': drips if drips and drips >= 1 else 2,
        'offset_m': round(offset, 2) if offset is not None and offset >= 0 else None,
        'varieties': varieties
    }


def _variety_for_index(varieties, index, total):
    """Pick the variety for the tree at `index` so each variety keeps its percentage share."""
    if not varieties or total <= 0:
        return None
    weights = [max(0.0, float(v.get('pct') or 0)) for v in varieties]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return varieties[0]
    position = (index + 0.5) / total * weight_sum
    cumulative = 0.0
    for variety, weight in zip(varieties, weights):
        cumulative += weight
        if position <= cumulative:
            return variety
    return varieties[-1]


@trees_bp.route('/<int:project_id>/trees/generate', methods=['POST'])
@login_required
def trees_generate(project_id):
    """Generate tree points along rows using each zone's spacing, drips and variety mix."""
    rows = list(mongo.db.tree_rows.find({'project_id': project_id}).sort('id', 1))
    if not rows:
        flash('No rows found. Create rows first / لا توجد صفوف. أنشئ الصفوف أولاً', 'warning')
        return redirect(url_for('trees.trees_view', project_id=project_id))

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    zone_setups = {}
    for zone in zones:
        setup = _zone_setup_from_form(request.form, zone['id'])
        if setup is None:
            continue
        zone_setups[zone['id']] = setup
        mongo.db.zones.update_one(
            {'id': zone['id'], 'project_id': project_id},
            {'$set': {'tree_setup': setup}}
        )

    # Shared "All Sectors / Zones" setup, used for zones without their own.
    all_setup = _zone_setup_from_form(request.form, 'all')
    # The first-tree offset is only offered in the shared card, so a zone with
    # its own spacing/variety setup still uses the shared offset unless it sets
    # one of its own.
    shared_offset = (all_setup or {}).get('offset_m') or 0.0

    # Legacy form-level values, used when a zone has no stored setup.
    fallback_spacing = request.form.get('spacing', default=4, type=float)
    fallback_variety = (request.form.get('variety') or 'Olive').strip()
    fallback_variety_ar = (request.form.get('variety_ar') or 'زيتون').strip()

    # Clear existing trees
    mongo.db.trees.delete_many({'project_id': project_id})

    from irrigation.models import get_next_id as _gid
    from irrigation.engineering.projection import projected_shape, unproject_shape, get_project_centroid

    # Get project for projection
    project_doc = mongo.db.projects.find_one({'id': project_id})
    lng, lat = get_project_centroid(project_doc) if project_doc else (0.0, 0.0)

    rows_by_zone = {}
    for row in rows:
        rows_by_zone.setdefault(row.get('zone_id'), []).append(row)

    total = 0
    for zone_id, zone_rows in rows_by_zone.items():
        setup = zone_setups.get(zone_id) or all_setup or {}
        tree_spacing = setup.get('spacing_m') or fallback_spacing
        drips = setup.get('drips_per_tree') or 2
        varieties = setup.get('varieties') or []
        offset = setup.get('offset_m')
        offset = max(0.0, float(shared_offset if offset is None else offset))
        if tree_spacing <= 0:
            continue

        # Lay out every tree of this zone first, so variety percentages apply
        # over the whole zone rather than per individual row.
        points = []
        for row in zone_rows:
            row_geom = projected_shape(row['geometry'], lng, lat)
            length = row_geom.length  # Now in meters, not degrees
            # Trees start "offset" metres into the row and fill the rest at the
            # requested spacing, so no tree sits on the row end.
            usable = length - offset
            if usable <= 0:
                continue
            num_trees = max(1, int(usable / tree_spacing))
            for i in range(num_trees + 1):
                fraction = i / num_trees if num_trees > 0 else 0
                point = row_geom.interpolate(offset + fraction * usable)
                points.append((row, i, unproject_shape(point, lng, lat)))

        for index, (row, i, point_geojson) in enumerate(points):
            variety = _variety_for_index(varieties, index, len(points))
            if variety:
                variety_en = variety.get('en') or fallback_variety
                variety_ar = variety.get('ar') or variety_en
                variety_pct = variety.get('pct')
            else:
                variety_en = fallback_variety
                variety_ar = fallback_variety_ar
                variety_pct = None

            tree = {
                'id': _gid('trees'),
                'project_id': project_id,
                'zone_id': row.get('zone_id'),
                'row_id': row['id'],
                'name_en': f'T{row["id"]}-{i+1}',
                'name_ar': f'شجرة {row["id"]}-{i+1}',
                'geometry': point_geojson,
                'variety_en': variety_en,
                'variety_ar': variety_ar,
                'variety_pct': variety_pct,
                'spacing_m': tree_spacing,
                'drips_per_tree': drips,
                'offset_m': round(offset, 2),
                'validated': False
            }
            mongo.db.trees.insert_one(tree)
            total += 1

    flash(f'Generated {total} trees / تم إنشاء {total} شجرة', 'success')
    return redirect(url_for('trees.trees_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/trees/validate', methods=['POST'])
@login_required
def trees_validate(project_id):
    mongo.db.trees.update_many(
        {'project_id': project_id},
        {'$set': {'validated': True}}
    )
    flash('Trees validated / تم التحقق من الأشجار', 'success')
    return redirect(url_for('components.valves', project_id=project_id))
