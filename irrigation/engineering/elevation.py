"""Terrain elevation analysis module.

Fetches elevation data from Open-Meteo Elevation API (free, no key required),
caches it in MongoDB, and provides terrain-aware scoring for AI regeneration.

Architecture:
- elevation_samples: individual cached coordinate -> elevation lookups
- project_elevation_models: per-project grid of elevation samples

Sampling strategy:
1. Project land boundary to UTM meters
2. Generate a regular grid over the bounding box
3. Keep points inside the boundary polygon
4. Add key points (centroid, water source, vertices)
5. Convert back to WGS84 lat/lng
6. Batch-fetch elevations from Open-Meteo API
7. Cache results for future regeneration runs
"""
import math
import hashlib
import json
import urllib.request
import urllib.parse
from datetime import datetime
from shapely.geometry import shape, Point, Polygon, MultiPolygon
from shapely.ops import unary_union

from irrigation.extensions import mongo
from irrigation.engineering.projection import (
    projected_shape, unproject_shape, get_project_centroid, project_geometry,
    haversine_length_meters
)


# Open-Meteo Elevation API
# POST https://api.open-meteo.com/v1/elevation
# Body: {"latitude": [lat1, lat2, ...], "longitude": [lng1, lng2, ...]}
# Response: {"elevation": [e1, e2, ...]}
OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# Max coordinates per API call (Open-Meteo supports up to 100 per request)
MAX_COORDS_PER_REQUEST = 100

# Default grid spacing in meters
DEFAULT_GRID_SPACING_M = 30

# Max total sample points
DEFAULT_MAX_POINTS = 300


def _hash_boundary(boundary_geojson):
    """Create a stable hash of the boundary geometry for cache keys."""
    boundary_str = json.dumps(boundary_geojson, sort_keys=True)
    return hashlib.md5(boundary_str.encode()).hexdigest()


def sample_polygon_grid(boundary_geojson, lng, lat, spacing_m=DEFAULT_GRID_SPACING_M, max_points=DEFAULT_MAX_POINTS):
    """Generate a grid of sample points inside the boundary polygon.

    Returns a list of dicts: {'lng': float, 'lat': float, 'x': float, 'y': float}
    where x/y are projected UTM coordinates.
    """
    boundary_projected = projected_shape(boundary_geojson, lng, lat)

    if boundary_projected.is_empty or boundary_projected.area <= 0:
        return []

    # Handle MultiPolygon: use the largest polygon or union
    if isinstance(boundary_projected, MultiPolygon):
        boundary_projected = max(boundary_projected.geoms, key=lambda g: g.area)

    bounds = boundary_projected.bounds  # (minx, miny, maxx, maxy)
    minx, miny, maxx, maxy = bounds

    width = maxx - minx
    height = maxy - miny

    if width <= 0 or height <= 0:
        return []

    # Calculate grid dimensions to stay within max_points
    area = width * height
    if spacing_m <= 0:
        spacing_m = DEFAULT_GRID_SPACING_M

    # Estimate grid cells
    cells_x = max(1, int(width / spacing_m))
    cells_y = max(1, int(height / spacing_m))
    total_cells = cells_x * cells_y

    # If too many cells, increase spacing
    if total_cells > max_points:
        ratio = math.sqrt(total_cells / max_points)
        spacing_m = spacing_m * ratio
        cells_x = max(1, int(width / spacing_m))
        cells_y = max(1, int(height / spacing_m))

    # Generate grid points inside the polygon
    points = []
    x = minx
    while x <= maxx and len(points) < max_points:
        y = miny
        while y <= maxy and len(points) < max_points:
            pt = Point(x, y)
            if boundary_projected.contains(pt) or boundary_projected.touches(pt):
                # Convert projected point back to WGS84
                pt_geojson = unproject_shape(pt, lng, lat)
                if pt_geojson and pt_geojson.get('coordinates'):
                    points.append({
                        'lng': pt_geojson['coordinates'][0],
                        'lat': pt_geojson['coordinates'][1],
                        'x': x,
                        'y': y
                    })
            y += spacing_m
        x += spacing_m

    # Add key points: centroid
    centroid = boundary_projected.centroid
    if not centroid.is_empty:
        cent_geojson = unproject_shape(centroid, lng, lat)
        if cent_geojson and cent_geojson.get('coordinates'):
            points.append({
                'lng': cent_geojson['coordinates'][0],
                'lat': cent_geojson['coordinates'][1],
                'x': centroid.x,
                'y': centroid.y,
                'is_centroid': True
            })

    # Add water source if available
    # (caller should pass water source separately)

    # Add polygon vertices
    try:
        exterior_coords = list(boundary_projected.exterior.coords)
        for coord in exterior_coords[:20]:  # Limit vertices
            pt = Point(coord)
            pt_geojson = unproject_shape(pt, lng, lat)
            if pt_geojson and pt_geojson.get('coordinates'):
                points.append({
                    'lng': pt_geojson['coordinates'][0],
                    'lat': pt_geojson['coordinates'][1],
                    'x': coord[0],
                    'y': coord[1],
                    'is_vertex': True
                })
    except Exception:
        pass

    # Deduplicate by rounded coordinates
    seen = set()
    unique_points = []
    for p in points:
        key = (round(p['lng'], 5), round(p['lat'], 5))
        if key not in seen:
            seen.add(key)
            unique_points.append(p)

    return unique_points[:max_points]


def fetch_open_meteo_elevations(points):
    """Fetch elevation data from Open-Meteo Elevation API.

    Takes a list of dicts with 'lat' and 'lng' keys.
    Returns a list of elevation values (floats) in meters.

    API: POST https://api.open-meteo.com/v1/elevation
    Body: {"latitude": [lat1, lat2, ...], "longitude": [lng1, lng2, ...]}
    Response: {"elevation": [e1, e2, ...]}
    """
    if not points:
        return []

    elevations = []

    # Process in chunks to respect API limits
    for i in range(0, len(points), MAX_COORDS_PER_REQUEST):
        chunk = points[i:i + MAX_COORDS_PER_REQUEST]
        lats = [p['lat'] for p in chunk]
        lngs = [p['lng'] for p in chunk]

        # Build POST body
        body = json.dumps({
            'latitude': lats,
            'longitude': lngs
        }).encode('utf-8')

        try:
            req = urllib.request.Request(
                OPEN_METEO_ELEVATION_URL,
                data=body,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                if 'elevation' in result:
                    elevations.extend(result['elevation'])
                else:
                    # API error for this chunk - fill with None
                    elevations.extend([None] * len(chunk))
        except Exception as e:
            # Network/API failure - fill with None
            elevations.extend([None] * len(chunk))

    return elevations


# The two-collection model has no elevation_samples collection.  Raw samples
# are only a short-lived lookup cache (the per-project elevation model is
# persisted inside the project), so keep them in process memory.
_ELEVATION_SAMPLE_CACHE = {}


def get_cached_elevation(lat, lng):
    """Check if elevation for a coordinate is already cached."""
    return _ELEVATION_SAMPLE_CACHE.get((round(lat, 5), round(lng, 5)))


def cache_elevation(lat, lng, elevation_m):
    """Cache a single elevation sample for the life of the process."""
    _ELEVATION_SAMPLE_CACHE[(round(lat, 5), round(lng, 5))] = elevation_m


def get_or_build_elevation_model(project_id, project_doc, spacing_m=None, max_points=None):
    """Get or build a cached elevation model for a project.

    Returns a dict with:
    - samples: list of {lng, lat, x, y, elevation_m}
    - stats: {min, max, mean, range, std}
    - source: 'open-meteo'
    - water_source_elevation: elevation at water source
    """
    if spacing_m is None:
        spacing_m = DEFAULT_GRID_SPACING_M
    if max_points is None:
        max_points = DEFAULT_MAX_POINTS

    boundary = project_doc.get('boundary')
    if not boundary or boundary.get('type') != 'Polygon':
        return None

    boundary_hash = _hash_boundary(boundary)

    # Check for cached model
    cached_model = mongo.db.project_elevation_models.find_one({
        'project_id': project_id,
        'boundary_hash': boundary_hash,
        'spacing_m': spacing_m
    })

    if cached_model:
        # Discard a stale cached model that has no usable elevation values
        # (e.g. an earlier API failure) so it is rebuilt instead of forcing
        # all terrain-aware scoring to stay neutral.
        if cached_model.get('stats'):
            cached_model['_id'] = str(cached_model['_id'])
            return cached_model
        mongo.db.project_elevation_models.delete_one({
            'project_id': project_id,
            'boundary_hash': boundary_hash,
            'spacing_m': spacing_m
        })

    # Build new elevation model
    lng, lat = get_project_centroid(project_doc)
    sample_points = sample_polygon_grid(boundary, lng, lat, spacing_m, max_points)

    if not sample_points:
        return None

    # Add water source point
    water_source = project_doc.get('water_source')
    if water_source and water_source.get('coordinates'):
        ws_lng, ws_lat = water_source['coordinates'][0], water_source['coordinates'][1]
        ws_projected, _ = project_geometry(water_source, lng, lat)
        sample_points.append({
            'lng': ws_lng,
            'lat': ws_lat,
            'x': ws_projected.x if ws_projected else 0,
            'y': ws_projected.y if ws_projected else 0,
            'is_water_source': True
        })

    # Check cache for individual points
    elevations = []
    uncached_indices = []

    for i, pt in enumerate(sample_points):
        cached = get_cached_elevation(pt['lat'], pt['lng'])
        if cached is not None:
            elevations.append(cached)
        else:
            elevations.append(None)
            uncached_indices.append(i)

    # Fetch uncached elevations from API
    if uncached_indices:
        uncached_points = [sample_points[i] for i in uncached_indices]
        fetched = fetch_open_meteo_elevations(uncached_points)

        for idx, elev in zip(uncached_indices, fetched):
            if elev is not None:
                elevations[idx] = elev
                cache_elevation(sample_points[idx]['lat'], sample_points[idx]['lng'], elev)

    # Build final samples list
    samples = []
    elev_values = []

    for i, pt in enumerate(sample_points):
        elev = elevations[i]
        sample = {
            'lng': pt['lng'],
            'lat': pt['lat'],
            'x': pt.get('x', 0),
            'y': pt.get('y', 0),
            'elevation_m': elev
        }
        if pt.get('is_centroid'):
            sample['is_centroid'] = True
        if pt.get('is_vertex'):
            sample['is_vertex'] = True
        if pt.get('is_water_source'):
            sample['is_water_source'] = True

        samples.append(sample)
        if elev is not None:
            elev_values.append(elev)

    # Calculate statistics
    stats = {}
    water_source_elev = None

    if elev_values:
        stats = {
            'min_elevation_m': min(elev_values),
            'max_elevation_m': max(elev_values),
            'mean_elevation_m': sum(elev_values) / len(elev_values),
            'range_m': max(elev_values) - min(elev_values),
            'std_elevation_m': _std_dev(elev_values),
            'sample_count': len(elev_values),
            'api_failures': len(sample_points) - len(elev_values)
        }

        # Extract water source elevation
        for s in samples:
            if s.get('is_water_source') and s.get('elevation_m') is not None:
                water_source_elev = s['elevation_m']

    model = {
        'project_id': project_id,
        'boundary_hash': boundary_hash,
        'spacing_m': spacing_m,
        'source': 'open-meteo',
        'samples': samples,
        'stats': stats,
        'water_source_elevation_m': water_source_elev,
        'created_at': datetime.utcnow()
    }

    # Save to database (only cache models that actually contain elevation data)
    if stats:
        try:
            mongo.db.project_elevation_models.insert_one(dict(model))
        except Exception:
            pass

    model['_id'] = str(model.get('_id', ''))

    return model


def _std_dev(values):
    """Calculate standard deviation."""
    if not values or len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def get_elevation_at_point(model, point_projected):
    """Interpolate elevation at a projected point using nearest-neighbor.

    Args:
        model: elevation model dict with 'samples' list
        point_projected: shapely Point in UTM meters

    Returns:
        float: elevation in meters, or None if no data
    """
    if not model or not model.get('samples'):
        return None

    samples = model['samples']
    if not samples:
        return None

    # Filter samples with elevation data
    valid = [s for s in samples if s.get('elevation_m') is not None]
    if not valid:
        return None

    # Nearest-neighbor interpolation
    min_dist = float('inf')
    nearest_elev = None

    for s in valid:
        dx = s['x'] - point_projected.x
        dy = s['y'] - point_projected.y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < min_dist:
            min_dist = dist
            nearest_elev = s['elevation_m']

    return nearest_elev


def elevation_metrics_for_polygon(model, polygon_projected):
    """Calculate elevation statistics for a projected polygon.

    Returns:
        dict with mean, min, max, range, std of elevation within the polygon
    """
    if not model or not model.get('samples'):
        return None

    samples = model['samples']
    elev_values = []

    for s in samples:
        if s.get('elevation_m') is None:
            continue
        pt = Point(s['x'], s['y'])
        if polygon_projected.contains(pt) or polygon_projected.touches(pt):
            elev_values.append(s['elevation_m'])

    if not elev_values:
        # Fallback: nearest-neighbor from polygon centroid
        centroid = polygon_projected.centroid
        elev = get_elevation_at_point(model, centroid)
        if elev is not None:
            return {
                'mean_elevation_m': elev,
                'min_elevation_m': elev,
                'max_elevation_m': elev,
                'range_m': 0.0,
                'std_elevation_m': 0.0,
                'sample_count': 1
            }
        return None

    return {
        'mean_elevation_m': sum(elev_values) / len(elev_values),
        'min_elevation_m': min(elev_values),
        'max_elevation_m': max(elev_values),
        'range_m': max(elev_values) - min(elev_values),
        'std_elevation_m': _std_dev(elev_values),
        'sample_count': len(elev_values)
    }


# 32 named cardinal points of the compass (clockwise from North), at 11.25°
# steps. The abbreviations are the traditional 32-wind compass-rose names.
COMPASS_POINTS = [
    'N', 'NbE', 'NNE', 'NEbN', 'NE', 'NEbE', 'ENE', 'EbN',
    'E', 'EbS', 'ESE', 'SEbE', 'SE', 'SEbS', 'SSE', 'SbE',
    'S', 'SbW', 'SSW', 'SWbS', 'SW', 'SWbW', 'WSW', 'WbS',
    'W', 'WbN', 'WNW', 'NWbW', 'NW', 'NWbN', 'NNW', 'NbW',
]

# Human-readable names for each of the 32 points (for the Orientation lists).
COMPASS_NAMES_EN = {
    'N': 'North', 'NbE': 'North by East', 'NNE': 'North-Northeast',
    'NEbN': 'Northeast by North', 'NE': 'Northeast', 'NEbE': 'Northeast by East',
    'ENE': 'East-Northeast', 'EbN': 'East by North', 'E': 'East',
    'EbS': 'East by South', 'ESE': 'East-Southeast', 'SEbE': 'Southeast by East',
    'SE': 'Southeast', 'SEbS': 'Southeast by South', 'SSE': 'South-Southeast',
    'SbE': 'South by East', 'S': 'South', 'SbW': 'South by West',
    'SSW': 'South-Southwest', 'SWbS': 'Southwest by South', 'SW': 'Southwest',
    'SWbW': 'Southwest by West', 'WSW': 'West-Southwest', 'WbS': 'West by South',
    'W': 'West', 'WbN': 'West by North', 'WNW': 'West-Northwest',
    'NWbW': 'Northwest by West', 'NW': 'Northwest', 'NWbN': 'Northwest by North',
    'NNW': 'North-Northwest', 'NbW': 'North by West',
}

COMPASS_NAMES_AR = {
    'N': 'شمال', 'NbE': 'شمال نحو الشرق', 'NNE': 'شمال-شمال شرق',
    'NEbN': 'شمال شرق نحو الشمال', 'NE': 'شمال شرق', 'NEbE': 'شمال شرق نحو الشرق',
    'ENE': 'شرق-شمال شرق', 'EbN': 'شرق نحو الشمال', 'E': 'شرق',
    'EbS': 'شرق نحو الجنوب', 'ESE': 'شرق-جنوب شرق', 'SEbE': 'جنوب شرق نحو الشرق',
    'SE': 'جنوب شرق', 'SEbS': 'جنوب شرق نحو الجنوب', 'SSE': 'جنوب-جنوب شرق',
    'SbE': 'جنوب نحو الشرق', 'S': 'جنوب', 'SbW': 'جنوب نحو الغرب',
    'SSW': 'جنوب-جنوب غرب', 'SWbS': 'جنوب غرب نحو الجنوب', 'SW': 'جنوب غرب',
    'SWbW': 'جنوب غرب نحو الغرب', 'WSW': 'غرب-جنوب غرب', 'WbS': 'غرب نحو الجنوب',
    'W': 'غرب', 'WbN': 'غرب نحو الشمال', 'WNW': 'غرب-شمال غرب',
    'NWbW': 'شمال غرب نحو الغرب', 'NW': 'شمال غرب', 'NWbN': 'شمال غرب نحو الشمال',
    'NNW': 'شمال-شمال غرب', 'NbW': 'شمال نحو الغرب',
}

# Legacy 8-point codes that predate the 32-point rose, mapped to their new code.
ORIENTATION_ALIASES = {'ES': 'SE', 'WN': 'NW'}


def compass_from_bearing(bearing_deg):
    """Map a bearing (0-360, clockwise from North) to the nearest of the 32 points."""
    if bearing_deg is None:
        return None
    step = 360.0 / len(COMPASS_POINTS)
    index = int(round((bearing_deg % 360.0) / step)) % len(COMPASS_POINTS)
    return COMPASS_POINTS[index]


# Canonical bearing (degrees, clockwise from North) for each compass point, so
# the label and its numeric orientation stay linked (N=0, NbE=11.25, NNE=22.5, ...).
ORIENTATION_DEGREES = {
    point: round(i * (360.0 / len(COMPASS_POINTS)), 2)
    for i, point in enumerate(COMPASS_POINTS)
}

# Ordered options for the Orientation dropdown lists in templates.
ORIENTATION_OPTIONS = [
    {
        'code': point,
        'en': COMPASS_NAMES_EN.get(point, point),
        'ar': COMPASS_NAMES_AR.get(point, point),
        'deg': ORIENTATION_DEGREES[point],
    }
    for point in COMPASS_POINTS
]


def aspect_for_polygon(model, polygon_projected):
    """Compute the terrain aspect (downhill direction) of a projected polygon.

    Fits a plane (z = a*x + b*y + c) to the elevation samples inside the
    polygon, then derives the downhill bearing and slope.

    Returns a dict:
        {
            'bearing_deg': float,   # downhill direction, clockwise from North
            'compass': one of the 32 named compass points (see COMPASS_POINTS),
            'slope_percent': float, # gradient magnitude * 100
            'slope_deg': float,
            'sample_count': int,
        }
    or ``None`` when there is not enough data (or terrain is flat).
    """
    if not model or not model.get('samples'):
        return None

    poly = polygon_projected
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)

    samples = [s for s in model['samples'] if s.get('elevation_m') is not None]
    if len(samples) < 3:
        return None

    inside = [
        s for s in samples
        if poly.contains(Point(s['x'], s['y'])) or poly.touches(Point(s['x'], s['y']))
    ]
    if len(inside) < 3:
        # Fall back to the nearest samples to the polygon centroid.
        centroid = poly.centroid
        inside = sorted(
            samples,
            key=lambda s: (s['x'] - centroid.x) ** 2 + (s['y'] - centroid.y) ** 2
        )[:12]
    if len(inside) < 3:
        return None

    n = float(len(inside))
    xm = sum(s['x'] for s in inside) / n
    ym = sum(s['y'] for s in inside) / n

    sxx = sxy = syy = sxz = syz = 0.0
    for s in inside:
        x = s['x'] - xm
        y = s['y'] - ym
        z = s['elevation_m']
        sxx += x * x
        sxy += x * y
        syy += y * y
        sxz += x * z
        syz += y * z

    det = sxx * syy - sxy * sxy
    if abs(det) < 1e-9:
        return None  # degenerate / collinear samples

    # Gradient of the fitted plane: steepest ascent = (a, b).
    a = (sxz * syy - syz * sxy) / det
    b = (syz * sxx - sxz * sxy) / det

    # Downhill direction points opposite the gradient (east = -a, north = -b).
    east, north = -a, -b
    magnitude = math.hypot(a, b)
    if magnitude < 1e-9:
        return None  # flat terrain — no meaningful orientation

    bearing = math.degrees(math.atan2(east, north)) % 360.0
    slope_deg = math.degrees(math.atan(magnitude))

    return {
        'bearing_deg': bearing,
        'compass': compass_from_bearing(bearing),
        'slope_percent': magnitude * 100.0,
        'slope_deg': slope_deg,
        'sample_count': len(inside),
    }


def build_elevation_heatmap_geojson(model):
    """Generate GeoJSON of elevation sample points for map display.

    Returns a GeoJSON FeatureCollection with Point features colored by elevation.
    """
    if not model or not model.get('samples'):
        return {'type': 'FeatureCollection', 'features': []}

    features = []
    elevations = [s['elevation_m'] for s in model['samples'] if s.get('elevation_m') is not None]

    if not elevations:
        return {'type': 'FeatureCollection', 'features': []}

    min_elev = min(elevations)
    max_elev = max(elevations)
    elev_range = max_elev - min_elev if max_elev > min_elev else 1

    for s in model['samples']:
        if s.get('elevation_m') is None:
            continue

        # Color interpolation: green (low) -> yellow -> red (high)
        ratio = (s['elevation_m'] - min_elev) / elev_range
        color = _elevation_color(ratio)

        feature = {
            'type': 'Feature',
            'properties': {
                'elevation_m': round(s['elevation_m'], 2),
                'color': color,
                'type': 'elevation_point'
            },
            'geometry': {
                'type': 'Point',
                'coordinates': [s['lng'], s['lat']]
            }
        }
        features.append(feature)

    return {
        'type': 'FeatureCollection',
        'features': features
    }


def _elevation_color(ratio):
    """Interpolate a color from green (low) to red (high) based on elevation ratio."""
    ratio = max(0, min(1, ratio))
    # Green -> Yellow -> Red
    if ratio < 0.5:
        # Green to Yellow
        r = int(255 * (ratio * 2))
        g = 200
        b = int(50 * (1 - ratio * 2))
    else:
        # Yellow to Red
        r = 255
        g = int(200 * (1 - (ratio - 0.5) * 2))
        b = 0
    return f'#{r:02x}{g:02x}{b:02x}'


def has_elevation_model(project_id):
    """Check if an elevation model exists for a project."""
    return mongo.db.project_elevation_models.find_one({'project_id': project_id}) is not None


def delete_elevation_model(project_id):
    """Delete cached elevation model for a project."""
    mongo.db.project_elevation_models.delete_many({'project_id': project_id})
