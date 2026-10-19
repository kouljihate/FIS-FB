"""KML/KMZ parsing and export utilities."""
import io
import zipfile
from fastkml import kml, styles
from lxml import etree

from irrigation.extensions import mongo
from irrigation.models import get_next_id


def parse_kml_content(kml_text):
    """Parse KML text and extract all polygons and points as GeoJSON."""
    k = kml.KML.parse(io.BytesIO(kml_text.encode('utf-8')))

    features = []

    def walk_feature(folder):
        folder_features = folder.features
        if callable(folder_features):
            folder_features = folder_features()

        for feature in folder_features:
            name = feature.name or ''
            feature_geometry = getattr(feature, 'geometry', None)
            if feature_geometry is not None:
                geom = getattr(feature_geometry, 'geometry', feature_geometry)
                geojson = _kml_geom_to_geojson(geom)
                if geojson:
                    features.append({
                        'name': name,
                        'geometry': geojson
                    })
            # Recurse into folders
            if hasattr(feature, 'features'):
                walk_feature(feature)

    walk_feature(k)

    return features


def parse_kmz_file(kmz_path):
    """Extract doc.kml from a KMZ file and parse it."""
    with zipfile.ZipFile(kmz_path, 'r') as zf:
        kml_files = [f for f in zf.namelist() if f.endswith('.kml')]
        if not kml_files:
            return []
        kml_data = zf.read(kml_files[0]).decode('utf-8')
    return parse_kml_content(kml_data)


def _kml_geom_to_geojson(geom):
    """Convert a shapely geometry from fastkml to GeoJSON dict."""
    if geom is None:
        return None

    from shapely.geometry import mapping
    return mapping(geom)


def export_project_to_kml(project_id):
    """Build a KML document from a project's geometry elements."""
    project = mongo.db.projects.find_one({'id': project_id})
    if not project:
        return None

    from shapely.geometry import shape as shp_shape

    k = kml.KML()
    doc = kml.Document(id='project-doc', name=project.get('name', 'Project'),
                       description='')
    k.append(doc)

    def add_feature(folder, feature_id, name, geojson):
        if not geojson:
            return
        try:
            geom = shp_shape(geojson)
        except Exception:
            return
        folder.append(kml.Placemark(id=feature_id, name=name, description='', geometry=geom))

    # Land boundary
    if project.get('boundary'):
        add_feature(doc, 'boundary', 'Land Boundary / حدود الأرض', project['boundary'])

    # Sectors
    sectors_folder = kml.Folder(id='sectors', name='Sectors / القطاعات')
    doc.append(sectors_folder)
    for sector in mongo.db.sectors.find({'project_id': project_id}):
        add_feature(sectors_folder, f'sector-{sector["id"]}',
                    sector.get('name_en', sector.get('name', '')), sector.get('polygon'))

    # Zones
    zones_folder = kml.Folder(id='zones', name='Zones / المناطق')
    doc.append(zones_folder)
    for zone in mongo.db.zones.find({'project_id': project_id}):
        add_feature(zones_folder, f'zone-{zone["id"]}',
                    zone.get('name_en', zone.get('name', '')), zone.get('polygon'))

    # Network elements (pipes, valves, etc.)
    net_folder = kml.Folder(id='network', name='Network / الشبكة')
    doc.append(net_folder)
    for elem in mongo.db.network_elements.find({'project_id': project_id}):
        add_feature(net_folder, f'elem-{elem["id"]}',
                    f"{elem.get('type', 'element')}", elem.get('geometry'))

    # Tree rows
    rows_folder = kml.Folder(id='rows', name='Tree Rows / صفوف الأشجار')
    doc.append(rows_folder)
    for row in mongo.db.tree_rows.find({'project_id': project_id}):
        add_feature(rows_folder, f'row-{row["id"]}',
                    'Tree Row / صف', row.get('geometry'))

    # Trees
    trees_folder = kml.Folder(id='trees', name='Trees / الأشجار')
    doc.append(trees_folder)
    for tree in mongo.db.trees.find({'project_id': project_id}):
        add_feature(trees_folder, f'tree-{tree["id"]}',
                    'Tree / شجرة', tree.get('geometry'))

    return etree.tostring(k.etree_element(), pretty_print=True, encoding='utf-8').decode('utf-8')
