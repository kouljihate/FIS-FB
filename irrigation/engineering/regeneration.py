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
from shapely.affinity import rotate as affine_rotate
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

#: Largest sector count the engine will build. The min/max area rules of a big
#: plot legitimately ask for far more than the old 12-piece limit — which
#: silently broke the requested sector size (12 pieces of 103,000 m² instead
#: of the ~10,000 m² asked for).
MAX_SECTORS = 200


def _min_part_fraction(n_parts):
    """Sliver threshold for a partition of ``n_parts`` pieces.

    Never above MIN_AREA_FRACTION, and never above half of an equal share —
    at 118 pieces every part is 0.85% of the land, so the fixed 5% would
    reject *every* candidate as a sliver.
    """
    return min(MIN_AREA_FRACTION, 0.5 / max(int(n_parts or 1), 1))

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


def _split_polygon_equal_area(polygon, n_parts, axis='x'):
    """Split a polygon into n_parts of approximately equal area.

    Cuts are made perpendicular to ``axis``: 'x' gives vertical strips
    (the classic sweep), 'y' gives horizontal bands — which is what a grid
    partition needs for its rows. Each cut sits at the coordinate that
    reaches the next 1/n of the area, found by binary search, so concave
    shapes stay fully covered.
    """
    bounds = polygon.bounds
    minx, miny, maxx, maxy = bounds
    total_area = polygon.area
    target_area = total_area / n_parts

    if total_area <= 0:
        return []

    parts = []
    current = minx if axis == 'x' else miny
    span_end = maxx if axis == 'x' else maxy

    def slice_up_to(value):
        clip_box = (box(current, miny, value, maxy) if axis == 'x'
                    else box(minx, current, maxx, value))
        return polygon.intersection(clip_box)

    for i in range(n_parts - 1):
        target_accumulated = target_area * (i + 1)
        accumulated = sum(p.area for p in parts)

        # Binary search for the coordinate that gives the target area.
        # 24 rounds pin the cut to span/2^24 (micrometres on a real plot) and
        # cost half as much as the old 50 — this runs once per piece per
        # candidate, so a 118-piece split pays for it 118 times.
        lo = current
        hi = span_end

        for _ in range(24):  # Binary search iterations
            mid = (lo + hi) / 2
            if slice_up_to(mid).area < target_accumulated - accumulated:
                lo = mid
            else:
                hi = mid

        # Final slice
        cut = (lo + hi) / 2
        # Handle MultiPolygon/GeometryCollection results
        clipped_polys = _extract_polygons(slice_up_to(cut))
        if clipped_polys:
            merged = unary_union(clipped_polys)
            parts.append(merged)
            current = cut

    # Last part
    last = polygon.intersection(
        box(current, miny, maxx, maxy) if axis == 'x'
        else box(minx, current, maxx, maxy))
    last_polys = _extract_polygons(last)
    if last_polys:
        merged = unary_union(last_polys)
        parts.append(merged)

    return parts


def _score_partition(parts, water_source_point=None, elevation_model=None,
                     water_source_elevation=None, weights=None, water_points=None):
    """Score a candidate partition based on multiple criteria.

    When elevation_model is available, adds two terrain-aware criteria:
    - Elevation uniformity: prefers sectors with similar internal elevation (contour bands)
    - Gravity pressure: prefers sectors at or below water source elevation (gravity flow)

    `water_points` optionally carries several projected sources (wells,
    basins, the legacy point): each part is then scored on its distance to
    the *nearest* one, so a split that puts every sector beside some source
    wins over one that only serves the project's single point.

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

    # 3. Water source access (average distance from each part centroid to
    # its nearest source — every well/basin counts when several are given)
    sources = list(water_points or ([water_source_point] if water_source_point else []))
    if sources:
        centroids = [p.centroid for p in parts]
        distances = [
            min(math.sqrt((c.x - s.x) ** 2 + (c.y - s.y) ** 2) for s in sources)
            for c in centroids
        ]
        avg_dist = sum(distances) / len(distances)
        if water_points:
            # Several sources: normalize against the size of the land, a fixed
            # denominator for every candidate of this run, so "the nearest
            # source is close" actually earns points instead of only
            # "the distances are equally spread".
            xs = [c.x for c in centroids]
            ys = [c.y for c in centroids]
            span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
            water_score = 1.0 - min(1.0, avg_dist / span) if span > 0 else 1.0
        else:
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
                    elevation_model=None, water_source_elevation=None, weights=None,
                    water_points=None):
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
        if merged.is_empty or merged.area < polygon.area * _min_part_fraction(n_parts):
            continue
        back = _rotate_polygon(merged, -angle, origin=rotated.centroid)
        original_parts.append(back)

    if len(original_parts) < n_parts:
        return None

    score = _score_partition(
        original_parts, water_source_point,
        elevation_model, water_source_elevation, weights, water_points=water_points
    )
    return {'parts': original_parts, 'score': score, 'angle': angle,
            'strategy': 'strips'}


def _try_grid_partition(polygon, n_rows, n_cols, angle, water_source_point=None,
                        elevation_model=None, water_source_elevation=None,
                        weights=None, water_points=None):
    """Partition into an ``n_rows x n_cols`` grid of equal-area cells.

    The grid is cut in the frame rotated by ``angle`` (the land's long axis),
    so a compact field breaks into compact blocks instead of the long strips
    the single-axis sweep can only produce. Returns ``None`` when the shape
    does not yield exactly ``n_rows * n_cols`` usable cells.
    """
    if n_rows < 1 or n_cols < 1 or n_rows * n_cols < 2:
        return None

    rotated = _rotate_polygon(polygon, angle)
    bands = _split_polygon_equal_area(rotated, n_rows, axis='y')
    if len(bands) != n_rows:
        return None

    cells = []
    for band in bands:
        cells.extend(_split_polygon_equal_area(band, n_cols, axis='x'))
    if len(cells) != n_rows * n_cols:
        return None

    origin = rotated.centroid
    parts = []
    for cell in cells:
        polys = _extract_polygons(cell)
        if not polys:
            return None
        merged = unary_union(polys)
        if merged.is_empty or merged.area < polygon.area * _min_part_fraction(n_rows * n_cols):
            return None
        parts.append(_rotate_polygon(merged, -angle, origin=origin))

    score = _score_partition(parts, water_source_point, elevation_model,
                             water_source_elevation, weights,
                             water_points=water_points)
    return {'parts': parts, 'score': score, 'angle': round(float(angle), 1),
            'strategy': f'grid {n_rows}x{n_cols}'}


def _planar_gradient(samples):
    """Least-squares plane fit z = a*x + b*y + c over elevation samples.

    Returns the gradient ``(a, b)`` in metres per projected metre, or ``None``
    when there is too little data or the terrain is flat (no direction).
    """
    valid = [s for s in (samples or []) if s.get('elevation_m') is not None]
    if len(valid) < 3:
        return None
    sx = np.array([s['x'] for s in valid], dtype=float)
    sy = np.array([s['y'] for s in valid], dtype=float)
    sz = np.array([s['elevation_m'] for s in valid], dtype=float)
    try:
        coef, *_ = np.linalg.lstsq(
            np.column_stack([sx, sy, np.ones_like(sx)]), sz, rcond=None
        )
        ga, gb = float(coef[0]), float(coef[1])
    except Exception:
        return None
    if not (math.isfinite(ga) and math.isfinite(gb)):
        return None
    if math.hypot(ga, gb) < 1e-9:
        return None
    return ga, gb


def _terrain_axes(elevation_model):
    """Terrain-derived split directions from the planar fit of the samples.

    Returns ``None`` when the fit is unusable (flat or too little data), else:
      - fallline_deg: downhill bearing, clockwise from north (0-360)
      - contour_deg: bearing of the contour lines, perpendicular (0-180)
      - relief_m: total elevation range of the land
    """
    if not elevation_model:
        return None
    grad = _planar_gradient(elevation_model.get('samples'))
    if grad is None:
        return None
    ga, gb = grad
    # x = easting, y = northing → bearing clockwise from north.
    uphill_deg = math.degrees(math.atan2(ga, gb)) % 360.0
    relief = float((elevation_model.get('stats') or {}).get('range_m') or 0)
    if relief <= 0:
        return None  # flat land: the directions carry no information
    return {
        'fallline_deg': (uphill_deg + 180.0) % 360.0,
        'contour_deg': (uphill_deg + 90.0) % 180.0,
        'relief_m': relief,
    }


def _principal_axis(polygon):
    """The land's long axis, from a PCA of its boundary vertices.

    Returns ``(axis_deg, elongation)``: ``axis_deg`` is the bearing of the long
    axis (clockwise from north, 0-180) and ``elongation`` the major/minor
    ratio. A shape with no dominant direction returns ``(None, elongation)``.
    """
    ring = list(polygon.exterior.coords)
    if ring and ring[0] == ring[-1]:
        ring = ring[:-1]  # don't count the closing vertex twice
    if len(ring) < 3:
        return None, 1.0
    xs = np.array([c[0] for c in ring], dtype=float)
    ys = np.array([c[1] for c in ring], dtype=float)
    xs = xs - xs.mean()
    ys = ys - ys.mean()
    cov = np.array([[(xs * xs).mean(), (xs * ys).mean()],
                    [(xs * ys).mean(), (ys * ys).mean()]])
    if not np.all(np.isfinite(cov)):
        return None, 1.0
    try:
        eigvals, eigvecs = np.linalg.eigh(cov)
    except Exception:
        return None, 1.0
    major = int(np.argmax(eigvals))
    minor = int(np.argmin(eigvals))
    l1, l2 = float(eigvals[major]), float(eigvals[minor])
    if l1 <= 1e-12:
        return None, 1.0
    vx = float(eigvecs[0, major])
    vy = float(eigvecs[1, major])
    axis_deg = math.degrees(math.atan2(vx, vy)) % 180.0
    elongation = math.sqrt(l1 / l2) if l2 > 1e-12 else 999.0
    if elongation < 1.15:
        return None, elongation  # no direction worth aligning a split to
    return axis_deg, elongation


def _grid_shapes(n_parts):
    """Factor pairs (rows, cols) of ``n_parts``, most square first."""
    if n_parts < 2:
        return []
    pairs = [(r, n_parts // r) for r in range(1, n_parts + 1)
             if n_parts % r == 0]
    # Most square first; rounded so (3,4) and (4,3) tie deterministically.
    pairs.sort(key=lambda rc: (round(abs(math.log(rc[0] / rc[1])), 9), rc[0]))
    return pairs


def split_sectors_axis_grid(polygon, n_parts, water_source_point=None,
                            elevation_model=None, water_source_elevation=None,
                            weights=None, water_points=None):
    """Cut the land into compact ``rows x cols`` blocks on its long axis.

    The layout that won the four-way study (contour bands, contour strips,
    fall-line strips, axis grid): for the same count the blocks keep far
    less elevation inside a sector than any strip layout, and won under
    every priority preset. Every factorization of ``n_parts`` is built in
    the frame of the principal axis and the highest-scoring one wins;
    1 x n is skipped because a single row is the strip sweep, not blocks.

    Returns the usual scored result dict, or ``None`` when the shape cannot
    deliver exactly ``n_parts`` blocks (a prime count, a very elongated
    land) — the caller then falls back to its other splitters.
    """
    axis_deg, _ = _principal_axis(polygon)
    grid_angle = axis_deg or 0.0
    best = None
    for rows, cols in _grid_shapes(n_parts):
        if rows == 1 or cols == 1:
            continue
        result = _try_grid_partition(polygon, rows, cols, grid_angle,
                                     water_source_point, elevation_model,
                                     water_source_elevation, weights,
                                     water_points=water_points)
        if result and (best is None or result['score'] > best['score']):
            best = result
    return best


def _envelope_frame(geom):
    """(long-edge angle, aspect ratio) of the geometry's oriented envelope.

    Works for Polygon, MultiPolygon and slivers alike (through the convex
    hull), and is the direction a cut should cross to shorten a piece.
    """
    hull = geom.convex_hull
    if hull.is_empty or hull.geom_type != 'Polygon':
        return None, 999.0
    rect = hull.minimum_rotated_rectangle
    if rect.is_empty or rect.geom_type != 'Polygon':
        return None, 999.0
    coords = list(rect.exterior.coords)
    edges = []
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        if length > 1e-9:
            edges.append((length, math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0))
    if not edges:
        return None, 999.0
    long_len, angle = max(edges, key=lambda e: e[0])
    short_len = min(e[0] for e in edges)
    aspect = long_len / short_len if short_len > 1e-9 else 999.0
    return angle, aspect


def _cut_equal_area(poly, line_deg, frac):
    """Split ``poly`` so the children lie on either side along ``line_deg``.

    ``line_deg`` is the direction of separation — the dividing line runs
    perpendicular to it. The kept part holds ``frac`` of the area, found by
    bisection on the offset (area is monotone in it), and both children are
    rotated back. Returns ``(kept, rest)`` or ``(None, None)`` when no
    offset yields both sides non-empty.
    """
    if poly is None or poly.is_empty or poly.area <= 0:
        return None, None
    frac = min(max(float(frac), 1e-6), 1.0 - 1e-6)
    frame = affine_rotate(poly, -float(line_deg), origin=(0, 0))
    minx, miny, maxx, maxy = frame.bounds
    span = 2.0 * max(maxx - minx, maxy - miny) + 10.0
    target = frame.area * frac
    lo, hi = minx, maxx
    for _ in range(32):
        mid = (lo + hi) / 2.0
        left = frame.intersection(box(minx - span, miny - span, mid, maxy + span))
        if left.is_empty or left.area < target:
            lo = mid
        else:
            hi = mid
    mid = (lo + hi) / 2.0
    left = frame.intersection(box(minx - span, miny - span, mid, maxy + span))
    right = frame.intersection(box(mid, miny - span, maxx + span, maxy + span))
    if left.is_empty or right.is_empty or left.area <= 0 or right.area <= 0:
        return None, None
    return (affine_rotate(left, float(line_deg), origin=(0, 0)),
            affine_rotate(right, float(line_deg), origin=(0, 0)))


def _cut_recursive(poly, k, extra_cut_angles, elevation_model, relief, depth=0):
    """Recursively cut ``poly`` into exactly ``k`` equal-area pieces.

    Each node divides the piece with a line holding ``m/k`` of its area
    (``m = k // 2``), so every leaf ends at exactly ``area / k``. Two cut
    directions compete at every node: across the piece's own longest
    dimension (the k-d move — keeps children square), and along the
    terrain contour when one was given (separates elevation). The
    direction whose children score better wins: worst child aspect plus a
    weighted share of the elevation range each child would carry.

    Returns the list of ``k`` pieces, or ``None`` when a cut fails — the
    caller then falls back to its next splitter.
    """
    if k <= 1:
        return [poly]
    if depth > 32:
        return None

    angle, _ = _envelope_frame(poly)
    # Separation runs along the given angle, so cutting along the long axis
    # is what divides a long piece in half (the k-d move).
    along_long = angle % 180.0 if angle is not None else 0.0
    candidates = [along_long]
    for extra in extra_cut_angles:
        delta = (extra - along_long) % 180.0
        if min(delta, 180.0 - delta) >= 5.0:  # not a duplicate direction
            candidates.append(extra % 180.0)

    m = k // 2
    frac = m / float(k)
    best = None
    best_penalty = None
    for cand in candidates:
        left, right = _cut_equal_area(poly, cand, frac)
        if left is None:
            continue
        penalty = max(_envelope_frame(left)[1], _envelope_frame(right)[1])
        if elevation_model and relief > 0:
            for child in (left, right):
                metrics = elevation_metrics_for_polygon(elevation_model, child)
                if metrics:
                    penalty += 0.5 * (metrics.get('range_m') or 0.0) / relief
        if best_penalty is None or penalty < best_penalty:
            best_penalty = penalty
            best = (left, right)
    if best is None:
        return None

    left_parts = _cut_recursive(best[0], m, extra_cut_angles,
                                elevation_model, relief, depth + 1)
    if left_parts is None:
        return None
    right_parts = _cut_recursive(best[1], k - m, extra_cut_angles,
                                 elevation_model, relief, depth + 1)
    if right_parts is None:
        return None
    return left_parts + right_parts


def split_sectors_adaptive(polygon, n_parts, water_source_point=None,
                           elevation_model=None, water_source_elevation=None,
                           weights=None, water_points=None):
    """Cut the land into exactly ``n_parts`` equal-area blocks that follow
    its shape and terrain — the adaptive splitter.

    Instead of forcing one global grid or strip sweep on every piece of the
    land, the polygon is bisected recursively: each cut holds exactly
    ``m/k`` of the current piece's area and crosses that piece's own longest
    dimension, competing with the terrain-contour direction when elevation
    data is available. Interior pieces come out close to square (a k-d
    partition of the mass), pieces near the boundary take the perimeter's
    shape, and every sector lands on exactly ``total / n_parts`` m².

    Returns the usual scored result dict, or ``None`` when a cut fails —
    the registry then tries the next splitter.
    """
    if polygon is None or polygon.is_empty or polygon.area <= 0:
        return None
    if n_parts is None or n_parts < 1:
        return None

    extra_cut_angles = []
    relief = 0.0
    if elevation_model and (elevation_model.get('stats') or {}).get('range_m'):
        relief = float(elevation_model['stats']['range_m'])
    if elevation_model and relief > 5.0:
        terrain = _terrain_axes(elevation_model)
        if terrain:
            # Separating along the fall line leaves the dividing line on the
            # contour: the two children then sit at clearly different
            # elevations, which the penalty below can prefer.
            extra_cut_angles.append(terrain['fallline_deg'] % 180.0)

    parts = _cut_recursive(polygon, n_parts, extra_cut_angles,
                           elevation_model, relief)
    if not parts or len(parts) != n_parts:
        return None
    score = _score_partition(parts, water_source_point, elevation_model,
                             water_source_elevation, weights,
                             water_points=water_points)
    return {'parts': parts, 'score': score, 'angle': 'adaptive',
            'strategy': 'adaptive'}


#: Sector split functions the smart Auto-Generate runs, in order: the first
#: one that returns a result decides the layout. Every entry is
#: ``(name, splitter)`` with the signature of :func:`split_sectors_adaptive`
#: (returns a scored result dict, or ``None`` when it cannot cut the
#: requested count). To add a new way of splitting the land, define a
#: function with that signature and append it here — ``regenerate_sectors``
#: picks it up with no other change.
SMART_SPLITTERS = [
    ('adaptive', split_sectors_adaptive),
    ('axis_grid', split_sectors_axis_grid),
]


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

    # Least-squares planar fit → gradient vector (a, b); None when flat.
    grad = _planar_gradient(samples)
    if grad is None:
        return []  # flat terrain — weight has no discriminating direction
    ga, gb = grad

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
                             water_source_elevation=None, weights=None,
                             water_points=None):
    """Build an elevation contour-band partition and score it."""
    parts = _split_polygon_by_elevation(polygon, n_parts, elevation_model)
    if len(parts) < n_parts:
        return None
    score = _score_partition(parts, water_source_point, elevation_model,
                             water_source_elevation, weights,
                             water_points=water_points)
    return {'parts': parts, 'score': score, 'angle': 'elevation',
            'strategy': 'elevation_bands'}


def _project_water_points(project, lng, lat):
    """Projected points for every water source the project lists.

    A basin or reservoir footprint collapses to its centroid; when the project
    only carries the legacy single Point, that point is returned. ``[]`` means
    the project has no positioned source at all.
    """
    points = []
    sources = project.get('water') or (project.get('spec') or {}).get('water_sources') or []
    for source in sources:
        geo = None
        raw = source.get('polygon')
        if isinstance(raw, dict) and raw.get('type') == 'Polygon':
            geo = raw
        elif isinstance(raw, (list, tuple)):
            ring = []
            for pos in raw:
                try:
                    ring.append([float(pos[0]), float(pos[1])])
                except (TypeError, ValueError, IndexError):
                    ring = []
                    break
            if len(ring) >= 3:
                if ring[0] != ring[-1]:
                    ring.append(list(ring[0]))
                geo = {'type': 'Polygon', 'coordinates': [ring]}
        if geo is None:
            try:
                geo = {'type': 'Point',
                       'coordinates': [float(source.get('lng')), float(source.get('lat'))]}
            except (TypeError, ValueError):
                continue
        try:
            projected, _ = project_geometry(geo, lng, lat)
        except Exception:
            continue
        if projected is None or projected.is_empty:
            continue
        points.append(projected if isinstance(projected, Point) else projected.centroid)

    if points:
        return points

    legacy = project.get('water_source') or {}
    if legacy.get('type') == 'Point' and legacy.get('coordinates'):
        try:
            projected, _ = project_geometry(legacy, lng, lat)
            if isinstance(projected, Point) and not projected.is_empty:
                points.append(projected)
        except Exception:
            pass
    return points


def regenerate_sectors(project_id, n_sectors=None, config=None):
    """AI regeneration: optimally divide land boundary into sectors.

    The smart path cuts the land with the functions registered in
    SMART_SPLITTERS (axis grid blocks on the land's long axis first);
    anything they cannot cut falls back to a scored sweep over split angles
    and elevation bands, judged on area balance, compactness, water-source
    proximity, shape regularity and terrain elevation.

    `config` (all optional) supports the Auto Generate modal inputs:
      - smart: bool — smart split: the sector count comes from the min/max
        area rules alone, the land is cut by the functions registered in
        SMART_SPLITTERS (the adaptive equal-area splitter first, the axis
        grid as fallback), and scoring uses every water point of the project
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
    smart = bool(config.get('smart'))

    project = mongo.db.projects.find_one({'_id': project_id})
    if not project:
        project = mongo.db.projects.find_one({'id': project_id})
    if not project:
        return []

    boundary = project.get('boundary')
    if not boundary or boundary.get('type') != 'Polygon':
        return []

    # Determine number of sectors. Smart with no number typed in means "let
    # the land decide": the count is derived from the area rules below.
    if n_sectors is None:
        n_sectors = 0 if smart else (config.get('n_sectors') or 4)  # 0 = derive below

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

    # Sector count: a number typed into the modal is what the user gets.
    # The min/max area rules only derive a count when the field was left
    # empty — they used to override the typed value on large lands (6 became
    # 12), which made the modal look like it ignored the input.
    min_area = config.get('min_sector_area_m2')
    max_area = config.get('max_sector_area_m2')
    if n_sectors:
        n_sectors = max(1, min(int(n_sectors), MAX_SECTORS))
    else:
        total_area = boundary_shapely.area
        if max_area and float(max_area) > 0:
            n_sectors = int(math.ceil(total_area / float(max_area)))
        if min_area and float(min_area) > 0:
            upper = max(1, int(total_area // float(min_area)))
            n_sectors = min(n_sectors or upper, upper)
        # The min-area rule already bounds the count; the cap only catches a
        # missing/absurdly small max on a huge plot.
        n_sectors = max(1, min(n_sectors or 0, MAX_SECTORS))
    if not n_sectors:
        n_sectors = 4  # no count typed and no rules to derive one from

    # Water source point in projected coordinates. A smart split scores every
    # source the project has (wells, basins, the legacy point), not just the
    # single project point.
    water_point = None
    water_points = None
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
        if smart:
            water_points = _project_water_points(project, lng, lat) or None
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

    # Smart split: read the land first — its long axis and, when the terrain
    # is usable, the contour and fall-line directions — then add those cut
    # directions to the fixed 5° sweep. Every candidate is scored; the best
    # partition wins, so a wrong hint can never beat a better split.
    axis_deg, elongation = (None, 1.0)
    terrain = None
    extra_angles = []
    if smart:
        axis_deg, elongation = _principal_axis(boundary_shapely)
        if axis_deg is not None:
            extra_angles.extend([axis_deg, (axis_deg + 90.0) % 180.0])
        if has_elevation:
            terrain = _terrain_axes(elevation_model)
            if terrain:
                extra_angles.extend([terrain['contour_deg'],
                                     terrain['fallline_deg'] % 180.0])

    # Splitter registry: the smart path runs SMART_SPLITTERS in order and
    # the first one able to cut the count decides the layout — the adaptive
    # equal-area splitter first (it follows each piece's shape and terrain),
    # then the axis grid. The scored sweep stays as the fallback for shapes
    # no splitter can cut, and is the whole path when smart is off.
    best_result = None
    best_score = -1.0
    if smart:
        for _splitter_name, splitter in SMART_SPLITTERS:
            best_result = splitter(
                boundary_shapely, n_sectors,
                water_source_point=water_point,
                elevation_model=elevation_model,
                water_source_elevation=water_source_elevation,
                weights=weights, water_points=water_points)
            if best_result:
                best_score = best_result['score']
                break

    if best_result is None:
        # Try all angles and find the best partition. A many-piece split
        # costs a binary-search cut per piece per candidate, so the fixed 5°
        # sweep would take minutes on a 100+ sector plot: coarsen it with
        # the count (the land-derived directions below are still tried
        # exactly).
        sweep = list(SPLIT_ANGLES) if n_sectors <= 24 else list(range(0, 180, 15))
        for angle in sweep + extra_angles:
            result = _try_partition(
                boundary_shapely, n_sectors, angle, water_point,
                elevation_model, water_source_elevation, weights,
                water_points=water_points
            )
            if result and result['score'] > best_score:
                best_score = result['score']
                best_result = result

        # Elevation-aware contour bands, when real terrain data is available.
        if has_elevation:
            elev_result = _try_elevation_partition(
                boundary_shapely, n_sectors, elevation_model,
                water_point, water_source_elevation, weights,
                water_points=water_points
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

    strategy = best_result.get('strategy')
    if not strategy:
        strategy = best_result.get('angle') if isinstance(best_result.get('angle'), str) else 'strips'
    ai_config_summary = {
        'n_sectors': n_sectors,
        'use_elevation': has_elevation,
        'water_source_mode': water_mode,
        'priority': priority,
        'inset_m': inset_m,
        'strategy': strategy,
    }
    if smart:
        # What the smart split read off the land, kept with the config.
        ai_config_summary.update({
            'smart': True,
            'axis_deg': round(axis_deg, 1) if axis_deg is not None else None,
            'elongation': round(float(elongation), 2),
            'terrain': ({'relief_m': round(terrain['relief_m'], 2),
                         'fallline_deg': round(terrain['fallline_deg'], 1),
                         'contour_deg': round(terrain['contour_deg'], 1)}
                        if terrain else None),
            'water_points': len(water_points or []),
        })

    for i, part in enumerate(best_result['parts']):
        # Unproject back to WGS84
        sector_geojson = unproject_shape(part, lng, lat)

        # Prefix + increment with no space (Sector1, Sector2, …), the same
        # shape as the KML placemark ids the project is built from.
        name_en = f"{name_prefix_en}{i + 1}"
        name_ar = f"{name_prefix_ar}{i + 1}"

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
                     orientation=None, sector_orientations=None, sector_ids=None):
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

    ``sector_ids`` narrows the rebuild to those sectors (Auto Generate Zones with
    a subset ticked); ``sector_id`` still takes precedence for the single-sector
    callers.
    """
    target_orientation = _target_orientation(orientation)
    sector_orientations = sector_orientations or {}

    query = {'project_id': project_id}
    if sector_id:
        query['id'] = sector_id
    elif sector_ids:
        query['id'] = {'$in': list(sector_ids)}

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