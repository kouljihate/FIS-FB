from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.models import serialize_doc, assign_short_names
import json

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/project/<int:project_id>/geometry')
@login_required
def get_geometry(project_id):
    """Return all geometry elements for a project as GeoJSON FeatureCollection.

    ``?types=sector,zone`` narrows the payload to those feature types (the
    string ``all`` keeps everything).  Big orchards are tens of thousands of
    trees and driplines, so a map that opens on the sector layout must not
    download, parse and draw the whole project.

    ``?sector=<id>`` keeps every sector outline (so the other boundaries stay
    visible) but only the marks that belong to that sector: its zones, rows,
    trees and network elements.  The land boundary and the imported KML
    overlays are left out of that view; the water sources stay, because they
    are the project's water supply and not part of any one sector.
    """
    from irrigation.access import find_project

    project = find_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    wanted = _wanted_types(request.args.get('types', ''))
    sector_scope = _sector_scope(request.args.get('sector'))
    scoped_sector = None
    if sector_scope is not None:
        scoped_sector = mongo.db.sectors.find_one({'project_id': project_id, 'id': sector_scope})
        if not scoped_sector:
            return jsonify({'error': 'Sector not found'}), 404
    # Shape used to place hand-drawn elements that carry no sector/zone link.
    sector_shape = _shape_of(scoped_sector.get('polygon')) if scoped_sector else None

    # Membership of the scoped sector: its zones and the rows inside them.
    zone_ids = []
    row_ids = []
    if sector_scope is not None:
        zone_ids = [zone['id'] for zone in mongo.db.zones.find(
            {'project_id': project_id, 'sector_id': sector_scope}, {'id': 1})]
        row_ids = [row['id'] for row in mongo.db.tree_rows.find(
            {'project_id': project_id, 'zone_id': {'$in': zone_ids}}, {'id': 1})]

    sector_levels = _short_name_levels(wanted)
    if sector_levels:
        assign_short_names(project_id, levels=sector_levels)

    features = []

    # Land boundary
    if sector_scope is None and project.get('boundary') and _accepted(wanted, 'boundary'):
        features.append({
            'type': 'Feature',
            'properties': {'name': 'Land Boundary', 'name_ar': 'حدود الأرض', 'type': 'boundary', 'color': '#28a745'},
            'geometry': project['boundary']
        })

    # Water sources: every well, basin and other source the project lists.
    # ``water_source`` is the legacy single Point kept in sync for the rest of
    # the app, so it is only drawn when the list has no positioned source.
    # A scoped sector view keeps them too: the basin or well feeds the whole
    # project, and hiding it made the basin look missing on the Map Overview.
    if _accepted(wanted, 'water_source'):
        features.extend(_water_source_features(project))

    # Sectors - a scoped view always keeps the boundaries for context.
    if sector_scope is not None or _accepted(wanted, 'sector'):
        for sector in mongo.db.sectors.find({'project_id': project_id}):
            if sector.get('polygon'):
                features.append({
                    'type': 'Feature',
                    'properties': {
                        'name': sector.get('name_en', ''),
                        'name_ar': sector.get('name_ar', ''),
                        'short_name': sector.get('short_name', ''),
                        'type': 'sector',
                        'color': '#007bff',
                        'id': sector['id'],
                        'validated': sector.get('validated', False)
                    },
                    'geometry': sector['polygon']
                })

    # Zones
    if _accepted(wanted, 'zone'):
        from irrigation.engineering.elevation import ORIENTATION_DEGREES
        zone_query = {'project_id': project_id}
        if sector_scope is not None:
            zone_query['sector_id'] = sector_scope
        for zone in mongo.db.zones.find(zone_query):
            if zone.get('polygon'):
                orientation = zone.get('orientation')
                orientation_deg = zone.get('orientation_deg')
                if orientation_deg is None and orientation:
                    orientation_deg = ORIENTATION_DEGREES.get(orientation)
                features.append({
                    'type': 'Feature',
                    'properties': {
                        'name': zone.get('name_en', ''),
                        'name_ar': zone.get('name_ar', ''),
                        'short_name': zone.get('short_name', ''),
                        'type': 'zone',
                        'color': '#ffc107',
                        'id': zone['id'],
                        'sector_id': zone.get('sector_id'),
                        'validated': zone.get('validated', False),
                        'orientation': orientation,
                        'orientation_deg': orientation_deg
                    },
                    'geometry': zone['polygon']
                })

    # Network elements
    if _has_network_types(wanted):
        for elem in mongo.db.network_elements.find({'project_id': project_id}):
            elem_type = elem.get('type', '')
            if not elem.get('geometry') or not _accepted(wanted, elem_type):
                continue
            if sector_scope is not None and not _element_in_sector(
                    elem, sector_scope, zone_ids, sector_shape):
                continue
            features.append({
                'type': 'Feature',
                'properties': {
                    'name': elem.get('name_en', ''),
                    'name_ar': elem.get('name_ar', ''),
                    'type': elem_type,
                    'color': _get_elem_color(elem_type),
                    'id': elem['id'],
                    'validated': elem.get('validated', False)
                },
                'geometry': elem['geometry']
            })

    # Tree rows
    if _accepted(wanted, 'tree_row'):
        row_query = {'project_id': project_id}
        if sector_scope is not None:
            row_query['zone_id'] = {'$in': zone_ids}
        for row in mongo.db.tree_rows.find(row_query):
            if row.get('geometry'):
                features.append({
                    'type': 'Feature',
                    'properties': {
                        'name': row.get('name_en', ''),
                        'name_ar': row.get('name_ar', ''),
                        'short_name': row.get('short_name', ''),
                        'type': 'tree_row',
                        'color': '#6c757d',
                        'id': row['id'],
                        'zone_id': row.get('zone_id'),
                        'validated': row.get('validated', False)
                    },
                    'geometry': row['geometry']
                })

    # Trees
    if _accepted(wanted, 'tree'):
        tree_query = {'project_id': project_id}
        if sector_scope is not None:
            tree_query['$or'] = [
                {'zone_id': {'$in': zone_ids}},
                {'row_id': {'$in': row_ids}},
            ]
        for tree in mongo.db.trees.find(tree_query):
            if tree.get('geometry'):
                features.append({
                    'type': 'Feature',
                    'properties': {
                        'name': tree.get('name_en', ''),
                        'name_ar': tree.get('name_ar', ''),
                        'short_name': tree.get('short_name', ''),
                        'type': 'tree',
                        'color': '#28a745',
                        'id': tree['id'],
                        'row_id': tree.get('row_id'),
                        'variety_en': tree.get('variety_en', ''),
                        'variety_ar': tree.get('variety_ar', ''),
                        'drips_per_tree': tree.get('drips_per_tree', 2)
                    },
                    'geometry': tree['geometry']
                })

    # KML features that are not represented by the boundary or project collections.
    if sector_scope is None and _accepted(wanted, 'kml_feature'):
        for imported in project.get('kml_features', []):
            if imported.get('category') != 'overlay' or not imported.get('geometry'):
                continue
            features.append({
                'type': 'Feature',
                'properties': {
                    'name': imported.get('name', ''),
                    'name_ar': imported.get('name_ar', ''),
                    'type': 'kml_feature',
                    'color': '#6f42c1'
                },
                'geometry': imported['geometry']
            })

    return jsonify({
        'type': 'FeatureCollection',
        'features': features
    })


#: Network element types the geometry endpoint can emit.
NETWORK_TYPES = (
    'main_pipe', 'second_pipe', 'sub_pipe', 'dripline',
    'master_valve', 'sector_valve', 'zone_valve', 'check_valve',
    'connector', 'reduction', 'air_release', 'pressure_reduction',
    'terminator',
)


def _wanted_types(raw):
    """Requested feature types as a set; empty means "no restriction"."""
    if not raw:
        return set()
    wanted = {part.strip() for part in str(raw).split(',') if part.strip()}
    if 'all' in wanted or '*' in wanted:
        return set()
    return wanted


def _accepted(wanted, feature_type):
    return not wanted or feature_type in wanted


def _has_network_types(wanted):
    return not wanted or any(feature_type in wanted for feature_type in NETWORK_TYPES)


#: Marker colour and wording for each kind of water source.
WATER_SOURCE_KINDS = {
    'well': ('Well', 'بئر', '#17a2b8'),
    'basin': ('Basin', 'حوض', '#0dcaf0'),
    'reservoir': ('Reservoir', 'خزان', '#198754'),
    'pump': ('Pump', 'مضخة', '#ffc107'),
    'canal': ('Canal', 'قناة', '#6c757d'),
}


def _water_source_features(project):
    """One point per positioned water source, with a kind-specific colour."""
    sources = project.get('water') or (project.get('spec') or {}).get('water_sources') or []
    features = []
    for index, source in enumerate(sources):
        try:
            lat = float(source.get('lat'))
            lng = float(source.get('lng'))
        except (TypeError, ValueError):
            continue
        kind = str(source.get('type') or 'other').strip().lower()
        label_en, label_ar, color = WATER_SOURCE_KINDS.get(
            kind, ('Water Source', 'مصدر المياه', '#17a2b8'))
        name = str(source.get('name') or '').strip() or label_en
        features.append({
            'type': 'Feature',
            'properties': {
                'name': name,
                'name_ar': label_ar,
                'type': 'water_source',
                'source_type': kind,
                'id': index,
                'flow_lpm': source.get('flow_lpm'),
                'color': color,
            },
            'geometry': {'type': 'Point', 'coordinates': [lng, lat]},
        })
    if features:
        return features

    # Older or hand-built projects only carry the single legacy Point.
    legacy = project.get('water_source') or {}
    if legacy.get('type') == 'Point' and legacy.get('coordinates'):
        return [{
            'type': 'Feature',
            'properties': {
                'name': 'Water Source', 'name_ar': 'مصدر المياه',
                'type': 'water_source', 'source_type': 'well', 'id': 0,
                'color': WATER_SOURCE_KINDS['well'][2],
            },
            'geometry': legacy,
        }]
    return []


def _sector_scope(raw):
    """Parse ``?sector=<id>``; anything else means "the whole project"."""
    if raw is None:
        return None
    text = str(raw).strip()
    return int(text) if text.isdigit() else None


def _shape_of(geometry):
    """Shapely geometry for a GeoJSON dict, or None when it cannot be read."""
    if not geometry:
        return None
    try:
        from shapely.geometry import shape
        return shape(geometry)
    except Exception:
        return None


def _same_id(value, wanted):
    return value is not None and wanted is not None and str(value) == str(wanted)


def _element_in_sector(elem, sector_id, zone_ids, sector_shape):
    """Whether a network element is one of the marks of the scoped sector.

    Generated elements carry the sector or zone they serve; anything else (the
    main pipe, the master valve, hand-drawn fittings) is placed by geometry: an
    element that runs through the sector belongs to it, one that does not is
    left out of the view.
    """
    properties = elem.get('properties') or {}
    if _same_id(elem.get('sector_id'), sector_id) or _same_id(properties.get('sector_id'), sector_id):
        return True
    zone_ids = {str(zone_id) for zone_id in zone_ids}
    for value in (elem.get('zone_id'), properties.get('zone_id')):
        if value is not None and str(value) in zone_ids:
            return True
    if sector_shape is None:
        return False
    shape = _shape_of(elem.get('geometry'))
    if shape is None:
        return False
    try:
        return bool(shape.intersects(sector_shape))
    except Exception:
        return False


def _short_name_levels(wanted):
    """Which short-name levels the requested types can actually display."""
    if not wanted:
        return None
    levels = []
    if 'sector' in wanted:
        levels.append('sector')
    if 'zone' in wanted:
        levels.append('zone')
    if 'tree_row' in wanted:
        levels.append('row')
    if 'tree' in wanted:
        levels.append('tree')
    return levels


def _get_elem_color(elem_type):
    colors = {
        'main_pipe': '#dc3545',
        'second_pipe': '#0d6efd',
        'sub_pipe': '#fd7e14',
        'master_valve': '#6610f2',
        'sector_valve': '#6f42c1',
        'zone_valve': '#e83e8c',
        'dripline': '#20c997',
        'check_valve': '#17a2b8',
        'air_release': '#ffc107',
        'pressure_reducer': '#fd7e14',
        'reduction': '#198754',
        'other': '#6c757d',
    }
    return colors.get(elem_type, '#6c757d')
