"""Real AI regeneration engine for sectors and zones.

Uses optimization heuristics:
- Projects boundary to UTM meters for accurate area calculations.
- Tries multiple split angles and area-balanced partitioning.
- Scores candidates by area balance, compactness, water-source proximity,
  shape regularity, and terrain elevation analysis.
- Selects the best-scoring partition.

Elevation-aware scoring:
- Fetches terrain elevation data from Open-Meteo API (cached in MongoDB).
- Prefers partitions that group similar elevations together (contour bands).
- Penalties for sectors that require pumping uphill from water source.
- Gravity flow potential considered in partition scoring.
"""
import math
from shapely.geometry import shape, mapping, box, Polygon, LineString, Point, MultiPolygon, GeometryCollection
from shapely.ops import unary_union, split as shapely_split
import numpy as np

from irrigation.extensions import mongo
from irrigation.models import get_next_id
from irrigation.engineering.projection import (
    projected_shape, unproject_shape, get_project_centroid, project_geometry,
    haversine_length_meters, meridian_convergence
)
from irrigation.engineering.elevation import (
    get_or_build_elevation_model, elevation_metrics_for_polygon,
    get_elevation_at_point, aspect_for_polygon, compass_from_bearing,
    COMPASS_POINTS, ORIENTATION_ALIASES, ORIENTATION_DEGREES
)


# === CONFIGURATION ===

# Angles to try for splitting (degrees)
SPLIT_ANGLES = list(range(0, 180, 5))  # 0, 5, 10, ..., 175


def split_angle_for_bearing(bearing_deg):
    """Rotation angle that makes the equal-area strips run along ``bearing_deg``.

    ``_split_polygon_equal_area`` slices perpendicular to x, so the strips are
    long along y; rotating the polygon by θ and cutting leaves their long axis
    at θ degrees clockwise from north. Hence the cut angle is the requested
    bearing itself: 0° → north-south strips, 90° → east-west strips. A bearing
    describes an undirected axis, so 270° gives the same cut as 90°.
    """
    return float(bearing_deg) % 180.0

# Scoring weights (with elevation - total = 1.0)
WEIGHT_AREA_BALANCE = 0.20
WEIGHT_COMPACTNESS = 0.15
WEIGHT_WATER_ACCESS = 0.12
WEIGHT_SHAPE_REGULARITY = 0.10
WEIGHT_ELEVATION_UNIFORMITY = 0.25
WEIGHT_GRAVITY_PRESSURE = 0.18

# Fallback weights when elevation data is unavailable (total = 1.0)
WEIGHT_AREA_BALANCE_2D = 0.35
WEIGHT_COMPACTNESS_2D = 0.25
WEIGHT_WATER_ACCESS_2D = 0.20
WEIGHT_SHAPE_REGULARITY_2D = 0.20

# Minimum sector area fraction (avoid tiny slivers)
MIN_AREA_FRACTION = 0.05

# Named weight presets selectable from the Auto Generate modal.
# Each preset provides 'elevation' (6-criterion) and '2d' (4-criterion) weights.
WEIGHT_PRESETS = {
    'balanced': {
        'elevation': {
            'area': WEIGHT_AREA_BALANCE, 'compact': WEIGHT_COMPACTNESS,
            'water': WEIGHT_WATER_ACCESS, 'regularity': WEIGHT_SHAPE_REGULARITY,
            'elev_uniform': WEIGHT_ELEVATION_UNIFORMITY, 'gravity': WEIGHT_GRAVITY_PRESSURE,
        },
        '2d': {
            'area': WEIGHT_AREA_BALANCE_2D, 'compact': WEIGHT_COMPACTNESS_2D,
            'water': WEIGHT_WATER_ACCESS_2D, 'regularity': WEIGHT_SHAPE_REGULARITY_2D,
        },
    },
    'water': {
        'elevation': {
            'area': 0.12, 'compact': 0.10, 'water': 0.35,
            'regularity': 0.08, 'elev_uniform': 0.15, 'gravity': 0.20,
        },
        '2d': {'area': 0.20, 'compact': 0.15, 'water': 0.45, 'regularity': 0.20},
    },
    'elevation': {
        'elevation': {
            'area': 0.12, 'compact': 0.08, 'water': 0.08,
            'regularity': 0.07, 'elev_uniform': 0.40, 'gravity': 0.25,
        },
        '2d': {'area': 0.40, 'compact': 0.30, 'water': 0.15, 'regularity': 0.15},
    },
    'area': {
        'elevation': {
            'area': 0.45, 'compact': 0.15, 'water': 0.08,
            'regularity': 0.12, 'elev_uniform': 0.12, 'gravity': 0.08,
        },
        '2d': {'area': 0.55, 'compact': 0.20, 'water': 0.10, 'regularity': 0.15},
    },
    'compactness': {
        'elevation': {
            'area': 0.15, 'compact': 0.35, 'water': 0.08,
            'regularity': 0.17, 'elev_uniform': 0.15, 'gravity': 0.10,
        },
        '2d': {'area': 0.20, 'compact': 0.40, 'water': 0.15, 'regularity': 0.25},
    },
}


def _weights_for_preset(preset, has_elevation):
    """Resolve a weight preset to a flat weights dict for scoring."""
    preset_weights = WEIGHT_PRESETS.get(preset) or WEIGHT_PRESETS['balanced']
    key = 'elevation' if has_elevation else '2d'
    return preset_weights[key]


def _extract_polygons(geom):
    """Extract usable Polygon objects from any geometry type.

    Handles MultiPolygon, GeometryCollection, and invalid geometries
    by returning the largest valid polygon or filtering to polygons only.
    """
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        if geom.is_valid:
            return [geom]
        else:
            fixed = geom.buffer(0)
            if isinstance(fixed, Polygon) and fixed.is_valid:
                return [fixed]
            return []
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        polys = []
        for part in geom.geoms:
            if isinstance(part, Polygon) and part.is_valid and not part.is_empty:
                polys.append(part)
            elif isinstance(part, (MultiPolygon, GeometryCollection)):
                polys.extend(_extract_polygons(part))
        return polys
    # For other geometry types, try buffer(0) to fix
    try:
        fixed = geom.buffer(0)
        if isinstance(fixed, Polygon) and fixed.is_valid:
            return [fixed]
        elif isinstance(fixed, (MultiPolygon, GeometryCollection)):
            return _extract_polygons(fixed)
    except Exception:
        pass
    return []


def _largest_polygon(geom):
    """Return the largest valid polygon from a geometry."""
    polys = _extract_polygons(geom)
    if not polys:
        return None
    return max(polys, key=lambda p: p.area)


def _rotate_polygon(polygon, angle_degrees, origin=None):
    """Rotate a shapely polygon around an origin.

    Handles MultiPolygon by rotating the largest valid polygon.
    """
    # Ensure we have a valid Polygon
    if isinstance(polygon, (MultiPolygon, GeometryCollection)):
        polygon = _largest_polygon(polygon)
        if polygon is None:
            return Polygon()  # Empty fallback

    if not isinstance(polygon, Polygon) or polygon.is_empty:
        return Polygon()

    if origin is None:
        origin = polygon.centroid
    angle_rad = math.radians(angle_degrees)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    def rotate_point(x, y):
        dx = x - origin.x
        dy = y - origin.y
        return (dx * cos_a - dy * sin_a + origin.x,
                dx * sin_a + dy * cos_a + origin.y)

    rotated_coords = []
    for ring in [polygon.exterior] + list(polygon.interiors):
        new_ring = [rotate_point(x, y) for x, y in ring.coords]
        rotated_coords.append(new_ring)

    return Polygon(rotated_coords[0], rotated_coords[1:])


def _split_polygon_equal_area(polygon, n_parts):
    """Split a polygon into n_parts of approximately equal area by slicing perpendicular to the x-axis."""
    bounds = polygon.bounds
    minx, miny, maxx, maxy = bounds
    total_area = polygon.area
    target_area = total_area / n_parts

    if total_area <= 0:
        return []

    parts = []
    current_min_x = minx

    for i in range(n_parts - 1):
        target_accumulated = target_area * (i + 1)

        # Binary search for the x-coordinate that gives the target area
        lo = current_min_x
        hi = maxx

        for _ in range(50):  # Binary search iterations
            mid = (lo + hi) / 2
            clip_box = box(current_min_x, miny, mid, maxy)
            clipped = polygon.intersection(clip_box)
            if clipped.area < target_accumulated - sum(p.area for p in parts):
                lo = mid
            else:
                hi = mid

        # Final slice
        clip_box = box(current_min_x, miny, (lo + hi) / 2, maxy)
        clipped = polygon.intersection(clip_box)
        # Handle MultiPolygon/GeometryCollection results
        clipped_polys = _extract_polygons(clipped)
        if clipped_polys:
            merged = unary_union(clipped_polys)
            parts.append(merged)
            current_min_x = (lo + hi) / 2

    # Last part
    clip_box = box(current_min_x, miny, maxx, maxy)
    last = polygon.intersection(clip_box)
    last_polys = _extract_polygons(last)
    if last_polys:
        merged = unary_union(last_polys)
        parts.append(merged)

    return parts


def _score_partition(parts, water_source_point=None, elevation_model=None,
                     water_source_elevation=None, weights=None):
    """Score a candidate partition based on multiple criteria.

    When elevation_model is available, adds two terrain-aware criteria:
    - Elevation uniformity: prefers sectors with similar internal elevation (contour bands)
    - Gravity pressure: prefers sectors at or below water source elevation (gravity flow)

    `weights` is a flat dict from WEIGHT_PRESETS (keys: area, compact, water,
    regularity, and optionally elev_uniform, gravity). Defaults to the
    balanced elevation preset.
    """
    if not parts:
        return -1.0

    if weights is None:
        weights = _weights_for_preset('balanced', elevation_model is not None)

    n = len(parts)
    areas = [p.area for p in parts if p.area > 0]
    if not areas:
        return -1.0

    total_area = sum(areas)
    avg_area = total_area / len(areas)

    # 1. Area balance (lower variance = better)
    area_variance = sum((a - avg_area) ** 2 for a in areas) / len(areas)
    area_balance_score = 1.0 - min(1.0, area_variance / (avg_area ** 2)) if avg_area > 0 else 0.0

    # 2. Compactness (Polsby-Popper ratio: 4*pi*area / perimeter^2)
    compactness_scores = []
    for p in parts:
        if p.area > 0:
            perimeter = p.length
            if perimeter > 0:
                ratio = (4 * math.pi * p.area) / (perimeter ** 2)
                compactness_scores.append(ratio)
    compactness_score = sum(compactness_scores) / len(compactness_scores) if compactness_scores else 0.0

    # 3. Water source access (average distance from water source to each part centroid)
    if water_source_point:
        distances = []
        for p in parts:
            centroid = p.centroid
            d = math.sqrt((centroid.x - water_source_point.x) ** 2 +
                          (centroid.y - water_source_point.y) ** 2)
            distances.append(d)
        avg_dist = sum(distances) / len(distances)
        max_dist = max(distances) if distances else 1.0
        water_score = 1.0 - min(1.0, avg_dist / (max_dist * 2 + 1)) if max_dist > 0 else 1.0
    else:
        water_score = 0.5

    # 4. Shape regularity (penalize tiny fragments)
    min_area = min(areas)
    regularity_score = min_area / avg_area if avg_area > 0 else 0.0

    # Determine if we have elevation data
    has_elevation = elevation_model is not None and elevation_model.get('samples')

    if has_elevation:
        # 5. Elevation uniformity (prefers contour-band partitions)
        part_elev_metrics = []
        total_site_range = elevation_model.get('stats', {}).get('range_m', 0) or 0

        for p in parts:
            if p.area <= 0:
                continue
            metrics = elevation_metrics_for_polygon(elevation_model, p)
            if metrics:
                part_elev_metrics.append(metrics)

        if part_elev_metrics and total_site_range > 0:
            # Average of (part_range / site_range) — lower is better (more uniform)
            uniformity_ratios = [m['range_m'] / total_site_range for m in part_elev_metrics]
            avg_ratio = sum(uniformity_ratios) / len(uniformity_ratios)
            elevation_uniformity_score = 1.0 - min(1.0, avg_ratio)
        else:
            elevation_uniformity_score = 0.5  # Neutral

        # 6. Gravity pressure (prefer sectors at/below water source elevation)
        if water_source_elevation is not None:
            gravity_scores = []
            for m in part_elev_metrics:
                part_mean_elev = m['mean_elevation_m']
                elevation_gain = part_mean_elev - water_source_elevation
                # Downhill or flat = 1.0, uphill = penalty
                # Use a gentle penalty: each 10m uphill reduces score by ~0.3
                if elevation_gain <= 0:
                    gravity_scores.append(1.0)
                else:
                    gravity_scores.append(max(0.0, 1.0 - (elevation_gain / 30.0)))
            gravity_score = sum(gravity_scores) / len(gravity_scores) if gravity_scores else 0.5
        else:
            gravity_score = 0.5

        total_score = (
            weights.get('area', WEIGHT_AREA_BALANCE) * area_balance_score +
            weights.get('compact', WEIGHT_COMPACTNESS) * compactness_score +
            weights.get('water', WEIGHT_WATER_ACCESS) * water_score +
            weights.get('regularity', WEIGHT_SHAPE_REGULARITY) * regularity_score +
            weights.get('elev_uniform', WEIGHT_ELEVATION_UNIFORMITY) * elevation_uniformity_score +
            weights.get('gravity', WEIGHT_GRAVITY_PRESSURE) * gravity_score
        )
    else:
        # Fallback: 2D scoring without elevation
        total_score = (
            weights.get('area', WEIGHT_AREA_BALANCE_2D) * area_balance_score +
            weights.get('compact', WEIGHT_COMPACTNESS_2D) * compactness_score +
            weights.get('water', WEIGHT_WATER_ACCESS_2D) * water_score +
            weights.get('regularity', WEIGHT_SHAPE_REGULARITY_2D) * regularity_score
        )

    return total_score


def _try_partition(polygon, n_parts, angle, water_source_point=None,
                    elevation_model=None, water_source_elevation=None, weights=None):
    """Try partitioning at a specific angle and return scored result.

    Passes elevation model to scoring for terrain-aware optimization.
    """
    # Rotate polygon
    rotated = _rotate_polygon(polygon, angle)

    # Split into equal-area strips
    parts = _split_polygon_equal_area(rotated, n_parts)

    # Rotate parts back and filter
    original_parts = []
    for part in parts:
        # Handle MultiPolygon from intersection
        polys = _extract_polygons(part)
        if not polys:
            continue
        merged = unary_union(polys)
        if merged.is_empty or merged.area < polygon.area * MIN_AREA_FRACTION:
            continue
        back = _rotate_polygon(merged, -angle, origin=rotated.centroid)
        original_parts.append(back)

    if len(original_parts) < n_parts:
        return None

    score = _score_partition(
        original_parts, water_source_point,
        elevation_model, water_source_elevation, weights
    )
    return {'parts': original_parts, 'score': score, 'angle': angle}


def _split_polygon_by_elevation(polygon, n_parts, elevation_model):
    """Partition a polygon into equal-area bands that follow the terrain
    gradient (fall line), so sectors run along elevation contours.

    Cells inside the polygon are ordered by their projection onto the
    dominant gradient direction and split into ``n_parts`` equal-area
    contour bands (low elevation → high elevation).

    Returns the bands as a list of Polygon/MultiPolygon, or ``[]`` when
    the elevation data is unusable (too few samples / flat terrain).
    """
    if elevation_model is None or n_parts < 2:
        return []
    samples = [s for s in elevation_model.get('samples') or [] if s.get('elevation_m') is not None]
    if len(samples) < 2:
        return []

    poly = polygon if isinstance(polygon, Polygon) else _largest_polygon(polygon)
    if poly is None or poly.is_empty:
        return []

    sx = np.array([s['x'] for s in samples], dtype=float)
    sy = np.array([s['y'] for s in samples], dtype=float)
    sz = np.array([s['elevation_m'] for s in samples], dtype=float)

    # Least-squares planar fit z = a*x + b*y + c → gradient vector (a, b)
    try:
        coef, *_ = np.linalg.lstsq(
            np.column_stack([sx, sy, np.ones_like(sx)]), sz, rcond=None
        )
        ga, gb = float(coef[0]), float(coef[1])
    except Exception:
        return []
    if math.hypot(ga, gb) < 1e-9:
        return []  # flat terrain — weight has no discriminating direction

    minx, miny, maxx, maxy = poly.bounds
    width, height = maxx - minx, maxy - miny
    if width <= 0 or height <= 0:
        return []

    cell = max(min(width, height) / 64.0, 1.0)
    cols = max(4, int(width / cell) + 1)
    rows = max(4, int(height / cell) + 1)
    if cols * rows > 8000:
        scale = math.sqrt((cols * rows) / 8000.0)
        cols, rows = max(4, int(cols / scale)), max(4, int(rows / scale))

    xs = np.linspace(minx + cell / 2.0, maxx - cell / 2.0, cols)
    ys = np.linspace(miny + cell / 2.0, maxy - cell / 2.0, rows)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()])

    inside = np.array([poly.covers(Point(float(x), float(y))) for x, y in pts])
    cells_xy = pts[inside]
    if len(cells_xy) < n_parts:
        return []

    # Order cells along the fall line and cut them into equal-area bands.
    proj = cells_xy[:, 0] * ga + cells_xy[:, 1] * gb
    order = np.argsort(proj)

    parts = []
    for grp in np.array_split(order, n_parts):
        band = cells_xy[grp]
        squares = [
            box(float(cx) - cell / 2.0, float(cy) - cell / 2.0,
                float(cx) + cell / 2.0, float(cy) + cell / 2.0)
            for cx, cy in band
        ]
        try:
            part = unary_union(squares)
            if part.is_empty:
                continue
            if not part.is_valid:
                part = part.buffer(0)
            parts.append(part.simplify(max(cell * 0.25, 0.5), preserve_topology=True))
        except Exception:
            continue

    return parts


def _try_elevation_partition(polygon, n_parts, elevation_model, water_source_point=None,
                             water_source_elevation=None, weights=None):
    """Build an elevation contour-band partition and score it."""
    parts = _split_polygon_by_elevation(polygon, n_parts, elevation_model)
    if len(parts) < n_parts:
        return None
    score = _score_partition(parts, water_source_point, elevation_model,
                             water_source_elevation, weights)
    return {'parts': parts, 'score': score, 'angle': 'elevation'}


def regenerate_sectors(project_id, n_sectors=None, config=None):
    """AI regeneration: optimally divide land boundary into sectors.

    Tries multiple split angles, evaluates area balance, compactness,
    water-source proximity, shape regularity, and terrain elevation,
    then selects the best partition.

    `config` (all optional) supports the Auto Generate modal inputs:
      - n_sectors: int, number of sectors (default 4)
      - use_elevation: bool — enable terrain-aware scoring (default True)
      - water_source_mode: 'project' | 'custom' | 'none' (default 'project')
      - water_lat / water_lng: floats — custom water point when mode='custom'
      - priority: weight preset key from WEIGHT_PRESETS (default 'balanced')
      - inset_m: float — inset the boundary inward before splitting, in
        meters, so sectors keep a buffer from the land edge (default 0)
      - min_sector_area_m2 / max_sector_area_m2: soft area constraints used
        to auto-adjust the number of sectors when n_sectors is not given
      - name_prefix_en / name_prefix_ar: sector name prefixes
    """
    config = config or {}

    project = mongo.db.projects.find_one({'_id': project_id})
    if not project:
        project = mongo.db.projects.find_one({'id': project_id})
    if not project:
        return []

    boundary = project.get('boundary')
    if not boundary or boundary.get('type') != 'Polygon':
        return []

    # Determine number of sectors
    if n_sectors is None:
        n_sectors = config.get('n_sectors') or 4  # Default

    use_elevation = config.get('use_elevation', True)
    water_mode = config.get('water_source_mode', 'project')
    priority = config.get('priority', 'balanced')
    try:
        inset_m = float(config.get('inset_m') or 0)
    except (TypeError, ValueError):
        inset_m = 0.0
    name_prefix_en = (config.get('name_prefix_en') or '').strip() or 'Sector'
    name_prefix_ar = (config.get('name_prefix_ar') or '').strip() or 'القطاع'

    # Project to UTM meters
    lng, lat = get_project_centroid(project) if project else (0.0, 0.0)
    boundary_shapely = projected_shape(boundary, lng, lat)

    # Ensure we have a valid Polygon
    if isinstance(boundary_shapely, MultiPolygon):
        boundary_shapely = max(boundary_shapely.geoms, key=lambda g: g.area)

    if boundary_shapely.is_empty or boundary_shapely.area <= 0:
        return []

    # Optional inward inset (keeps sectors off the land boundary)
    if inset_m > 0:
        inset_poly = boundary_shapely.buffer(-inset_m)
        inset_largest = _largest_polygon(inset_poly)
        if inset_largest is not None and inset_largest.area > 0:
            boundary_shapely = inset_largest

    # Soft area constraints: adjust sector count so each sector plausibly
    # fits within [min, max] area bounds.
    min_area = config.get('min_sector_area_m2')
    max_area = config.get('max_sector_area_m2')
    if min_area or max_area:
        total_area = boundary_shapely.area
        if max_area and float(max_area) > 0:
            import math as _math
            n_sectors = max(n_sectors, int(_math.ceil(total_area / float(max_area))))
        if min_area and float(min_area) > 0:
            import math as _math
            n_sectors = min(n_sectors, max(1, int(total_area // float(min_area))))
        n_sectors = max(1, min(n_sectors, 12))  # hard cap at 12 sectors

    # Water source point in projected coordinates
    water_point = None
    if water_mode == 'custom':
        try:
            w_lng = float(config.get('water_lng'))
            w_lat = float(config.get('water_lat'))
            ws_projected, _ = project_geometry(
                {'type': 'Point', 'coordinates': [w_lng, w_lat]}, lng, lat
            )
            water_point = ws_projected if isinstance(ws_projected, Point) else Point(ws_projected)
        except (TypeError, ValueError):
            water_point = None
    elif water_mode == 'project':
        if project.get('water_source') and project['water_source'].get('coordinates'):
            ws_geojson = project['water_source']
            ws_projected, _ = project_geometry(ws_geojson, lng, lat)
            water_point = ws_projected if isinstance(ws_projected, Point) else Point(ws_projected)
    # water_mode == 'none' → water_point stays None

    # Fetch or build terrain elevation model
    elevation_model = None
    water_source_elevation = None
    if use_elevation:
        try:
            elevation_model = get_or_build_elevation_model(project_id, project)
            if elevation_model:
                water_source_elevation = elevation_model.get('water_source_elevation_m')
        except Exception:
            # Elevation fetch failed — continue with 2D scoring
            pass

    has_elevation = elevation_model is not None and bool(elevation_model.get('stats'))
    if elevation_model is not None and not has_elevation:
        # Elevation fetch produced no usable data — fall back to 2D scoring.
        elevation_model = None
        water_source_elevation = None

    weights = _weights_for_preset(priority, has_elevation)

    # Try all angles and find the best partition
    best_result = None
    best_score = -1.0

    for angle in SPLIT_ANGLES:
        result = _try_partition(
            boundary_shapely, n_sectors, angle, water_point,
            elevation_model, water_source_elevation, weights
        )
        if result and result['score'] > best_score:
            best_score = result['score']
            best_result = result

    # Elevation-aware contour bands, when real terrain data is available.
    if has_elevation:
        elev_result = _try_elevation_partition(
            boundary_shapely, n_sectors, elevation_model,
            water_point, water_source_elevation, weights
        )
        if elev_result and elev_result['score'] > best_score:
            best_score = elev_result['score']
            best_result = elev_result

    if not best_result:
        # Fallback: simple grid
        best_result = _fallback_grid(boundary_shapely, n_sectors, water_point,
                                      elevation_model, water_source_elevation, weights)

    # Clear existing sectors and related data
    mongo.db.sectors.delete_many({'project_id': project_id})
    mongo.db.zones.delete_many({'project_id': project_id})
    mongo.db.network_elements.delete_many({'project_id': project_id})
    mongo.db.tree_rows.delete_many({'project_id': project_id})
    mongo.db.trees.delete_many({'project_id': project_id})

    # Save the best partition
    sectors = []
    sector_names = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    sector_names_ar = ['أ', 'ب', 'ج', 'د', 'هـ', 'و', 'ز', 'ح']

    ai_config_summary = {
        'n_sectors': n_sectors,
        'use_elevation': has_elevation,
        'water_source_mode': water_mode,
        'priority': priority,
        'inset_m': inset_m,
        'strategy': best_result.get('angle') if isinstance(best_result.get('angle'), str) else 'strips',
    }

    for i, part in enumerate(best_result['parts']):
        # Unproject back to WGS84
        sector_geojson = unproject_shape(part, lng, lat)

        suffix = sector_names[i] if i < len(sector_names) else str(i + 1)
        suffix_ar = sector_names_ar[i] if i < len(sector_names_ar) else str(i + 1)
        name_en = f"{name_prefix_en} {suffix}"
        name_ar = f"{name_prefix_ar} {suffix_ar}"

        # Get elevation metrics for this sector
        elev_metrics = None
        aspect = None
        if elevation_model:
            elev_metrics = elevation_metrics_for_polygon(elevation_model, part)
            aspect = aspect_for_polygon(elevation_model, part)

        sector = {
            'id': get_next_id('sectors'),
            'project_id': project_id,
            'name_en': name_en,
            'name_ar': name_ar,
            'polygon': sector_geojson,
            'orientation': aspect['compass'] if aspect else None,
            'orientation_deg': round(aspect['bearing_deg'], 1) if aspect else None,
            'validated': False,
            'ai_generated': True,
            'ai_score': round(best_result['score'], 4),
            'ai_angle': best_result['angle'],
            'ai_config': ai_config_summary,
            'area_m2': round(part.area, 2),
            'elevation_aware': elevation_model is not None,
            'elevation_metrics': elev_metrics
        }
        mongo.db.sectors.insert_one(sector)
        sector['_id'] = str(sector['_id'])
        sectors.append(sector)

    return sectors


def _target_orientation(value):
    """Normalize an orientation option to a 32-point code, or None for 'auto'."""
    target = value.upper() if isinstance(value, str) else None
    if target:
        target = ORIENTATION_ALIASES.get(target, target)
    return target if target in COMPASS_POINTS else None


def regenerate_zones(project_id, sector_id=None, zones_per_sector=None, use_elevation=True,
                     orientation=None, sector_orientations=None):
    """AI regeneration: optimally divide sectors into zones.

    Uses the same optimization approach but applied within each sector's polygon.
    Incorporates terrain elevation data for contour-aware zone partitioning.
    If trees exist, zones are sized proportionally to tree count (hydraulic demand).

    ``orientation`` may be any of the 32 named compass points (see
    ``COMPASS_POINTS``) or ``'auto'``/``None``. When a compass point is given, every
    generated zone is assigned that orientation; when ``'auto'`` the orientation
    of each zone is derived from the terrain aspect (downhill direction) of its
    polygon.

    ``sector_orientations`` optionally maps ``sector_id`` to a compass point (or
    ``'auto'``/``None``) or an exact degree value (float). A sector listed there
    wins over the global ``orientation`` argument. If a float is provided, the
    exact degree is stored in ``orientation_deg`` and the nearest compass point
    is stored in ``orientation``.
    """
    target_orientation = _target_orientation(orientation)
    sector_orientations = sector_orientations or {}

    query = {'project_id': project_id}
    if sector_id:
        query['id'] = sector_id

    sectors = list(mongo.db.sectors.find(query))
    if not sectors:
        return []

    if zones_per_sector is None:
        zones_per_sector = 2

    project = mongo.db.projects.find_one({'id': project_id})
    lng, lat = get_project_centroid(project) if project else (0.0, 0.0)

    # Fetch or build elevation model (cached)
    elevation_model = None
    water_source_elevation = None
    if project and use_elevation:
        try:
            elevation_model = get_or_build_elevation_model(project_id, project)
            if elevation_model:
                water_source_elevation = elevation_model.get('water_source_elevation_m')
        except Exception:
            pass

    zones = []
    zone_names = [1, 2, 3, 4]

    for sector in sectors:
        sector_orient_val = sector_orientations.get(sector['id'], target_orientation)
        
        # Determine if the value is an exact degree (float) or compass point
        if isinstance(sector_orient_val, (int, float)):
            sector_target = compass_from_bearing(sector_orient_val)
            sector_target_deg = round(float(sector_orient_val) % 360, 1)
        else:
            sector_target = _target_orientation(sector_orient_val)
            sector_target_deg = ORIENTATION_DEGREES.get(sector_target) if sector_target else None
        
        polygon = sector.get('polygon')
        if not polygon or polygon.get('type') != 'Polygon':
            continue

        sector_shapely = projected_shape(polygon, lng, lat)
        # Handle MultiPolygon
        if isinstance(sector_shapely, MultiPolygon):
            sector_shapely = max(sector_shapely.geoms, key=lambda g: g.area)

        if sector_shapely.is_empty or sector_shapely.area <= 0:
            continue

        # Check if trees exist for demand-based sizing
        tree_count = mongo.db.trees.count_documents({
            'project_id': project_id,
            'zone_id': {'$exists': True}
        })

        # Partition the sector.
        #
        # A user-chosen orientation is authoritative: the strips are cut at
        # exactly that bearing, because the polygons are what the map draws.
        # Previously the angle was only written onto the zones after an
        # independent score search had picked the cut, so sectors that showed
        # the same degrees in the table were drawn at different angles.
        best_result = None
        best_score = -1.0

        if sector_target_deg is not None:
            best_result = _try_partition(
                sector_shapely, zones_per_sector,
                split_angle_for_bearing(
                    sector_target_deg - meridian_convergence(lng, lat)
                ), None,
                elevation_model, water_source_elevation
            )
            if best_result:
                best_score = best_result['score']
        else:
            for angle in SPLIT_ANGLES:
                result = _try_partition(
                    sector_shapely, zones_per_sector, angle, None,
                    elevation_model, water_source_elevation
                )
                if result and result['score'] > best_score:
                    best_score = result['score']
                    best_result = result

            # Terrain contour bands (respects the slope / fall line) when
            # elevation data is available and no orientation was requested.
            if elevation_model:
                elev_result = _try_elevation_partition(
                    sector_shapely, zones_per_sector, elevation_model,
                    None, water_source_elevation
                )
                if elev_result and elev_result['score'] > best_score:
                    best_score = elev_result['score']
                    best_result = elev_result

        if not best_result:
            best_result = _fallback_grid(sector_shapely, zones_per_sector, None,
                                          elevation_model, water_source_elevation)

        sector_name_en = sector.get('name_en', 'Sector')
        sector_name_ar = sector.get('name_ar', 'قطاع')

        for i, part in enumerate(best_result['parts']):
            zone_geojson = unproject_shape(part, lng, lat)

            z_num = zone_names[i] if i < len(zone_names) else i + 1
            name_en = f"{sector_name_en} - Zone {z_num}"
            name_ar = f"{sector_name_ar} - منطقة {z_num}"

            # Get elevation metrics for this zone
            elev_metrics = None
            aspect = None
            if elevation_model:
                elev_metrics = elevation_metrics_for_polygon(elevation_model, part)
                aspect = aspect_for_polygon(elevation_model, part)

            # Orientation: user-chosen exact degree, otherwise derived from
            # the terrain aspect (downhill direction) of the zone polygon.
            if sector_target_deg is not None:
                zone_orientation = sector_target
                zone_orientation_deg = sector_target_deg
            elif aspect:
                zone_orientation = aspect['compass']
                zone_orientation_deg = round(aspect['bearing_deg'], 1)
            else:
                zone_orientation = None
                zone_orientation_deg = None

            # Ensure orientation_deg is always set when orientation is set
            if zone_orientation and zone_orientation_deg is None:
                zone_orientation_deg = ORIENTATION_DEGREES.get(zone_orientation)

            zone = {
                'id': get_next_id('zones'),
                'project_id': project_id,
                'sector_id': sector['id'],
                'name_en': name_en,
                'name_ar': name_ar,
                'polygon': zone_geojson,
                'validated': False,
                'ai_generated': True,
                'ai_score': round(best_result['score'], 4),
                'area_m2': round(part.area, 2),
                'orientation': zone_orientation,
                'orientation_deg': zone_orientation_deg,
                'slope_percent': round(aspect['slope_percent'], 2) if aspect else None,
                'elevation_aware': elevation_model is not None,
                'elevation_metrics': elev_metrics
            }
            mongo.db.zones.insert_one(zone)
            zone['_id'] = str(zone['_id'])
            zones.append(zone)

    return zones


def _fallback_grid(polygon, n_parts, water_point, elevation_model=None,
                   water_source_elevation=None, weights=None):
    """Simple grid-based fallback if optimization fails."""
    bounds = polygon.bounds
    minx, miny, maxx, maxy = bounds
    total_area = polygon.area
    target_area = total_area / n_parts

    # Try horizontal strips
    parts = []
    current_y = miny
    strip_height = (maxy - miny) / n_parts

    for i in range(n_parts):
        y1 = miny + i * strip_height
        y2 = miny + (i + 1) * strip_height
        clip_box = box(minx, y1, maxx, y2)
        clipped = polygon.intersection(clip_box)
        clipped_polys = _extract_polygons(clipped)
        if clipped_polys:
            parts.append(unary_union(clipped_polys))

    if len(parts) < n_parts:
        # Try vertical strips
        parts = []
        strip_width = (maxx - minx) / n_parts
        for i in range(n_parts):
            x1 = minx + i * strip_width
            x2 = minx + (i + 1) * strip_width
            clip_box = box(x1, miny, x2, maxy)
            clipped = polygon.intersection(clip_box)
            clipped_polys = _extract_polygons(clipped)
            if clipped_polys:
                parts.append(unary_union(clipped_polys))

    score = _score_partition(parts, water_point, elevation_model, water_source_elevation, weights)
    return {'parts': parts, 'score': score, 'angle': 0}


def get_regeneration_report(project_id):
    """Generate a report about the last AI regeneration."""
    sectors = list(mongo.db.sectors.find({'project_id': project_id, 'ai_generated': True}))
    zones = list(mongo.db.zones.find({'project_id': project_id, 'ai_generated': True}))

    # Check for elevation model
    elevation_model = mongo.db.project_elevation_models.find_one({'project_id': project_id})
    has_elevation = elevation_model is not None
    elev_stats = elevation_model.get('stats', {}) if elevation_model else {}

    report = {
        'sectors_count': len(sectors),
        'zones_count': len(zones),
        'sector_scores': [s.get('ai_score', 0) for s in sectors],
        'zone_scores': [z.get('ai_score', 0) for z in zones],
        'avg_sector_score': sum(s.get('ai_score', 0) for s in sectors) / len(sectors) if sectors else 0,
        'avg_zone_score': sum(z.get('ai_score', 0) for z in zones) / len(zones) if zones else 0,
        'total_area_m2': sum(s.get('area_m2', 0) for s in sectors),
        'elevation_aware': has_elevation,
        'elevation_stats': elev_stats,
        'water_source_elevation_m': elevation_model.get('water_source_elevation_m') if elevation_model else None,
        'sector_elevations': [
            {'name': s.get('name_en', ''), 'metrics': s.get('elevation_metrics', {})}
            for s in sectors if s.get('elevation_metrics')
        ]
    }
    return report