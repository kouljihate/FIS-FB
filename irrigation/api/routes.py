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
    """
    from irrigation.access import find_project

    project = find_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    wanted = _wanted_types(request.args.get('types', ''))
    sector_levels = _short_name_levels(wanted)
    if sector_levels:
        assign_short_names(project_id, levels=sector_levels)

    features = []

    # Land boundary
    if project.get('boundary') and _accepted(wanted, 'boundary'):
        features.append({
            'type': 'Feature',
            'properties': {'name': 'Land Boundary', 'name_ar': 'حدود الأرض', 'type': 'boundary', 'color': '#28a745'},
            'geometry': project['boundary']
        })

    # Water source
    if project.get('water_source') and _accepted(wanted, 'water_source'):
        features.append({
            'type': 'Feature',
            'properties': {'name': 'Water Source', 'name_ar': 'مصدر المياه', 'type': 'water_source', 'color': '#17a2b8'},
            'geometry': project['water_source']
        })

    # Sectors
    if _accepted(wanted, 'sector'):
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
        for zone in mongo.db.zones.find({'project_id': project_id}):
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
        for row in mongo.db.tree_rows.find({'project_id': project_id}):
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
        for tree in mongo.db.trees.find({'project_id': project_id}):
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
    if _accepted(wanted, 'kml_feature'):
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
