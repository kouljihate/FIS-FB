"""Geometry engine for field boundaries.

Field boundaries arrive either as geographic lon/lat pairs (from KML/GeoJSON) or
as already-projected metric coordinates.  Areas and lengths only mean something
in metric space, so measurement first reprojects to the local UTM zone with
``pyproj``; an equirectangular approximation would be wrong by several percent
over a kilometre-scale field, which is unacceptable for an area that drives
sector sizing and, downstream, the whole cost estimate.

**Coordinate system is explicit.**  Guessing the CRS from the numbers alone is
unsafe once polygons are derived: a 100 m x 50 m strip in projected metres has
every coordinate inside +/-180 / +/-90, so it would be mistaken for degrees and
its area would come out roughly ten orders of magnitude too large.  Every
function therefore takes a ``geographic`` flag.  When it is ``None`` the CRS is
detected from the whole boundary, and the resolved value is then passed down to
every derived polygon, so a split or a union can never change the interpretation.

Polygon algebra (union, split) is delegated to ``shapely`` so that concavo or
self-touching field outlines behave predictably instead of relying on hand-rolled
clipping.  All public functions accept and return coordinates in the *input* CRS.

**Convention for `geographic`**
    ``True``  -> coordinates are lon/lat degrees (EPSG:4326)
    ``False`` -> coordinates are already metres in a projected CRS
    ``None``  -> detect from the supplied boundary
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from pyproj import Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point as ShapelyPoint,
    Polygon,
    box,
)
from shapely.ops import split as shapely_split
from shapely.ops import unary_union

Point = tuple[float, float]

#: A projected metre grid used in irrigation practice never spans the whole
#: globe, so values outside +/-180 / +/-90 can only be metres.
_GEO_X_LIMIT = 180.0
_GEO_Y_LIMIT = 90.0


class GeometryError(ValueError):
    """Raised when a boundary cannot be interpreted as a valid polygon."""


# --- coercion and CRS --------------------------------------------------------


def as_points(coords: Iterable[Any]) -> list[Point]:
    """Coerce raw input into a list of ``(x, y)`` float pairs.

    A repeated closing vertex is dropped, as GeoJSON/KML rings repeat the first
    point.
    """
    points: list[Point] = []
    for item in coords or []:
        if isinstance(item, dict):
            x = item.get("x", item.get("lon", item.get("lng")))
            y = item.get("y", item.get("lat"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y = item[0], item[1]
        else:
            raise GeometryError(f"cannot interpret vertex {item!r}")
        try:
            points.append((float(x), float(y)))
        except (TypeError, ValueError) as exc:
            raise GeometryError(f"vertex {item!r} is not numeric") from exc

    if len(points) > 2 and points[0] == points[-1]:
        points.pop()
    return points


def is_geographic(coords: Sequence[Point]) -> bool:
    """Whether a *whole* boundary looks like lon/lat degrees.

    Only meaningful for a complete field outline.  Never call this on a derived
    sub-polygon to decide its CRS - carry the parent's flag instead.
    """
    return all(
        -_GEO_X_LIMIT <= x <= _GEO_X_LIMIT and -_GEO_Y_LIMIT <= y <= _GEO_Y_LIMIT
        for x, y in coords
    )


def resolve_geographic(coords: Sequence[Point], geographic: bool | None = None) -> bool:
    """Return the CRS flag to use, detecting it only when none was supplied."""
    if geographic is None:
        return is_geographic(coords)
    return bool(geographic)


def _utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180.0) // 6.0) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def _transformers(coords: Sequence[Point]) -> tuple[Transformer, Transformer]:
    lon = sum(p[0] for p in coords) / len(coords)
    lat = sum(p[1] for p in coords) / len(coords)
    epsg = _utm_epsg(lon, lat)
    forward = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    inverse = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    return forward, inverse


def to_metric(coords: Sequence[Point],
              geographic: bool | None = None) -> tuple[list[Point], Transformer | None]:
    """Project ``coords`` to metres, returning them plus the inverse transform."""
    if not resolve_geographic(coords, geographic):
        return list(coords), None
    forward, inverse = _transformers(coords)
    return [forward.transform(x, y) for x, y in coords], inverse


def from_metric(coords: Sequence[Point],
                inverse: Transformer | None) -> list[Point]:
    if inverse is None:
        return [(round(x, 6), round(y, 6)) for x, y in coords]
    return [tuple(round(v, 9) for v in inverse.transform(x, y)) for x, y in coords]


# --- polygon helpers ---------------------------------------------------------


def to_polygon(coords: Sequence[Point]) -> Polygon:
    polygon = Polygon(coords)
    if polygon.is_empty or not polygon.is_valid:
        cleaned = polygon.buffer(0)
        if cleaned.is_empty:
            raise GeometryError("boundary does not enclose an area")
        polygon = cleaned if isinstance(cleaned, Polygon) else _largest_polygon(cleaned)
    return polygon


def _largest_polygon(geom) -> Polygon:
    if isinstance(geom, Polygon):
        return geom
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    if isinstance(geom, GeometryCollection):
        polys = [g for g in geom.geoms if isinstance(g, Polygon)]
        if polys:
            return max(polys, key=lambda g: g.area)
    raise GeometryError("operation produced no polygon")


# --- measurements ------------------------------------------------------------


def area_m2(coords: Iterable[Any], geographic: bool | None = None) -> float:
    """Planar area in square metres."""
    points = as_points(coords)
    if len(points) < 3:
        return 0.0
    metric, _ = to_metric(points, geographic)
    return float(to_polygon(metric).area)


def area_ha(coords: Iterable[Any], geographic: bool | None = None) -> float:
    return area_m2(coords, geographic) / 10_000.0


def perimeter_m(coords: Iterable[Any], geographic: bool | None = None) -> float:
    points = as_points(coords)
    if len(points) < 2:
        return 0.0
    metric, _ = to_metric(points, geographic)
    if len(metric) < 3:
        return _open_length(metric)
    return float(to_polygon(metric).length)


def _open_length(points: Sequence[Point]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def centroid(coords: Iterable[Any], geographic: bool | None = None) -> Point:
    """Area centroid returned in the input CRS."""
    points = as_points(coords)
    if len(points) < 3:
        if not points:
            return (0.0, 0.0)
        return (
            round(sum(p[0] for p in points) / len(points), 9),
            round(sum(p[1] for p in points) / len(points), 9),
        )
    metric, inverse = to_metric(points, geographic)
    c = to_polygon(metric).centroid
    return from_metric([(c.x, c.y)], inverse)[0]


def bounds(coords: Iterable[Any]) -> tuple[float, float, float, float]:
    points = as_points(coords)
    if not points:
        raise GeometryError("no vertices supplied")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def describe(coords: Iterable[Any], geographic: bool | None = None) -> dict[str, Any]:
    """Summary metrics used by every geometry table and the API."""
    points = as_points(coords)
    if len(points) < 3:
        return {
            "area_m2": 0.0, "area_ha": 0.0, "perimeter_m": 0.0,
            "centroid": list(centroid(points, geographic)),
            "vertices": len(points),
            "geographic": resolve_geographic(points, geographic) if points else True,
            "valid": False,
        }
    resolved = resolve_geographic(points, geographic)
    return {
        "area_m2": round(area_m2(points, resolved), 3),
        "area_ha": round(area_ha(points, resolved), 6),
        "perimeter_m": round(perimeter_m(points, resolved), 3),
        "centroid": list(centroid(points, resolved)),
        "vertices": len(points),
        "geographic": resolved,
        "valid": True,
    }


def compactness(coords: Iterable[Any], geographic: bool | None = None) -> float:
    """Isoperimetric ratio ``4*pi*A/P^2``: 1.0 is a circle, lower is more ragged.

    Useful for flagging elongated sectors, which are the ones that later produce
    poor emission uniformity in the hydraulics stage.
    """
    resolved = resolve_geographic(as_points(coords), geographic)
    perimeter = perimeter_m(coords, resolved)
    if perimeter <= 0:
        return 0.0
    return round(4.0 * math.pi * area_m2(coords, resolved) / (perimeter ** 2), 6)


# --- polygon algebra ---------------------------------------------------------


def merge_polygons(first: Iterable[Any], second: Iterable[Any],
                   geographic: bool | None = None) -> list[Point]:
    """Union two boundaries, returning the largest resulting ring."""
    a = as_points(first)
    b = as_points(second)
    if len(a) < 3 or len(b) < 3:
        raise GeometryError("both boundaries need at least 3 vertices")

    resolved = resolve_geographic(a, geographic)
    metric_a, inverse = to_metric(a, resolved)
    metric_b, _ = to_metric(b, resolved)
    merged = unary_union([to_polygon(metric_a), to_polygon(metric_b)])
    polygon = _largest_polygon(merged)
    return from_metric(list(polygon.exterior.coords), inverse)


def split_polygon(coords: Iterable[Any], mode: str = "auto", ratio: float = 0.5,
                  geographic: bool | None = None) -> list[list[Point]]:
    """Cut a boundary into two parts with a straight line through its centroid.

    ``mode`` selects the cutting axis: ``"x"`` splits vertically, ``"y"`` splits
    horizontally and ``"auto"`` cuts across the longer bounding-box axis, which
    yields the more even division.  ``ratio`` moves the cut line from 0.05 to 0.95
    along that axis.
    """
    points = as_points(coords)
    if len(points) < 3:
        raise GeometryError("need at least 3 vertices to split")

    resolved = resolve_geographic(points, geographic)
    ratio = min(max(float(ratio), 0.05), 0.95)
    metric, inverse = to_metric(points, resolved)
    polygon = to_polygon(metric)
    minx, miny, maxx, maxy = polygon.bounds
    span_x, span_y = maxx - minx, maxy - miny
    if span_x <= 0 and span_y <= 0:
        raise GeometryError("degenerate boundary cannot be split")

    if mode == "auto":
        mode = "x" if span_x >= span_y else "y"

    pad = max(span_x, span_y) * 0.05 + 1.0
    if mode == "x":
        cut = minx + span_x * ratio
        splitter = LineString([(cut, miny - pad), (cut, maxy + pad)])
    else:
        cut = miny + span_y * ratio
        splitter = LineString([(minx - pad, cut), (maxx + pad, cut)])

    pieces = [
        part for part in shapely_split(polygon, splitter).geoms
        if isinstance(part, Polygon) and part.area > 1e-9
    ]
    if len(pieces) < 2:
        raise GeometryError("the cutting line did not divide the boundary")

    pieces.sort(key=lambda p: p.area, reverse=True)
    return [from_metric(list(p.exterior.coords), inverse) for p in pieces[:2]]


def _cut_axis(bounds: tuple[float, float, float, float], mode: str) -> str:
    minx, miny, maxx, maxy = bounds
    if mode == "auto":
        return "x" if (maxx - minx) >= (maxy - miny) else "y"
    return "x" if mode == "x" else "y"


def _as_polygon(geom) -> Polygon | None:
    """Largest polygon of ``geom``, or ``None`` when there is no area."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return geom if geom.area > 1e-12 else None
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        polys = [g for g in geom.geoms if isinstance(g, Polygon) and g.area > 1e-12]
        if not polys:
            return None
        return max(polys, key=lambda g: g.area)
    return None


def _slice_off(polygon: Polygon, mode: str,
               target_area: float) -> tuple[Polygon | None, Polygon]:
    """Cut ``polygon`` so the low-side piece has exactly ``target_area``.

    The cut position is found by bisection on the coordinate, because area grows
    monotonically as the cutting line sweeps across the polygon.  Forty-eight
    halvings take the position to well below millimetre precision, which is far
    finer than any survey a field boundary comes from.
    """
    minx, miny, maxx, maxy = polygon.bounds
    pad = max(maxx - minx, maxy - miny) + 1.0
    low, high = (minx, maxx) if mode == "x" else (miny, maxy)

    def windowed(cut: float):
        if mode == "x":
            return polygon.intersection(box(minx - pad, miny - pad, cut, maxy + pad))
        return polygon.intersection(box(minx - pad, miny - pad, maxx + pad, cut))

    for _ in range(48):
        middle = (low + high) / 2.0
        if windowed(middle).area < target_area:
            low = middle
        else:
            high = middle

    cut = (low + high) / 2.0
    piece = _as_polygon(windowed(cut))
    if piece is None:
        return None, polygon
    return piece, polygon.difference(piece)


def split_into_n(coords: Iterable[Any], n: int, mode: str = "auto",
                 geographic: bool | None = None) -> list[list[Point]]:
    """Partition a boundary into ``n`` parts of *equal area*.

    Parts are sliced off one at a time along a single axis and each takes an
    equal share of what is left, so three sectors come out at one third each.
    Recursive halving is only exact for powers of two - it would give thirds of
    roughly 50 %, 25 % and 25 %, which is not acceptable when the sector area
    drives both the valve sizing and the cost estimate.
    """
    points = as_points(coords)
    n = max(int(n), 1)
    resolved = resolve_geographic(points, geographic)
    if n == 1:
        return [points]

    metric, inverse = to_metric(points, resolved)
    polygon = to_polygon(metric)
    axis = _cut_axis(polygon.bounds, mode)

    pieces: list[Polygon] = []
    remaining = polygon
    for index in range(n - 1):
        share = remaining.area / (n - index)
        piece, remaining = _slice_off(remaining, axis, share)
        if piece is None or piece.area <= 1e-12:
            break
        pieces.append(piece)

    tail = _as_polygon(remaining)
    if tail is not None:
        pieces.append(tail)

    parts = [from_metric(list(p.exterior.coords), inverse) for p in pieces]
    parts.sort(key=lambda p: area_m2(p, resolved), reverse=True)
    return parts


def strip_rows(coords: Iterable[Any], spacing_m: float, mode: str = "auto",
               geographic: bool | None = None) -> list[list[Point]]:
    """Carve a boundary into parallel row strips about ``spacing_m`` wide.

    Rows in an orchard or a vineyard are parallel bands, so the polygon is
    bisected repeatedly across the chosen axis.  The strip count follows from the
    bounding-box span divided by the spacing, which is what an irrigation designer
    counts on the ground.
    """
    points = as_points(coords)
    if len(points) < 3:
        raise GeometryError("need at least 3 vertices to lay out rows")
    if spacing_m <= 0:
        raise GeometryError("row spacing must be positive")

    resolved = resolve_geographic(points, geographic)
    metric, _ = to_metric(points, resolved)
    minx, miny, maxx, maxy = to_polygon(metric).bounds
    span_x, span_y = maxx - minx, maxy - miny
    if mode == "auto":
        mode = "y" if span_y <= span_x else "x"
    span = span_y if mode == "y" else span_x
    if span <= 0:
        raise GeometryError("degenerate boundary")

    count = max(1, int(span // spacing_m))
    return split_into_n(points, count, mode=mode, geographic=resolved)


def centerline_points(coords: Iterable[Any], spacing_m: float, *, inset: float = 0.0,
                      geographic: bool | None = None) -> list[Point]:
    """Sample plant positions along the long axis of a strip-shaped boundary.

    Row strips are long and thin, so their centreline is what a tree row follows.
    Points are sampled at ``spacing_m`` intervals on the axis-aligned medial line
    and kept only where they fall inside the polygon, which removes the ragged
    ends that unclipped sampling would produce.
    """
    points = as_points(coords)
    if len(points) < 3:
        raise GeometryError("need at least 3 vertices to place plants")
    if spacing_m <= 0:
        raise GeometryError("plant spacing must be positive")

    resolved = resolve_geographic(points, geographic)
    metric, inverse = to_metric(points, resolved)
    polygon = to_polygon(metric)
    minx, miny, maxx, maxy = polygon.bounds
    span_x, span_y = maxx - minx, maxy - miny
    centre = polygon.centroid

    # The pattern is centred on the strip and offset by half a spacing, so the
    # first and last plants sit clear of the boundary instead of on it.  A plant
    # placed exactly on the edge would also be dropped by the containment test
    # below, silently shortening the row by one.
    half = spacing_m / 2.0
    samples: list[Point] = []
    if span_x >= span_y:
        usable = span_x - 2 * inset
        count = max(1, int(usable // spacing_m))
        start = minx + inset + (usable - spacing_m * count) / 2.0 + half
        samples = [(start + index * spacing_m, centre.y) for index in range(count)]
    else:
        usable = span_y - 2 * inset
        count = max(1, int(usable // spacing_m))
        start = miny + inset + (usable - spacing_m * count) / 2.0 + half
        samples = [(centre.x, start + index * spacing_m) for index in range(count)]

    inside = [(p.x, p.y) for p in (ShapelyPoint(*s) for s in samples)
              if polygon.contains(p)]
    return from_metric(inside, inverse)


def point_in_polygon(coords: Iterable[Any], point: Any,
                     geographic: bool | None = None) -> bool:
    """Whether ``point`` lies inside ``coords``.

    The point is expressed in the same CRS as the ring, and both are projected
    with the *ring's* transformer so the two can never disagree about where the
    origin of the projection is.
    """
    points = as_points(coords)
    if len(points) < 3:
        return False
    probe = as_points([point])
    if not probe:
        return False

    if resolve_geographic(points, geographic):
        forward, _ = _transformers(points)
        metric = [forward.transform(x, y) for x, y in points]
        candidate = forward.transform(*probe[0])
    else:
        metric = list(points)
        candidate = probe[0]
    return to_polygon(metric).contains(ShapelyPoint(*candidate))
