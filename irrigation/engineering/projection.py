from typing import Any, Dict, List, Tuple, Union

import math
from functools import lru_cache
import pyproj
from shapely.geometry import shape, Point, mapping
from shapely.ops import transform as shapely_transform

# WGS84 geographic CRS
wgs84 = pyproj.CRS("EPSG:4326")
EARTH_RADIUS_METERS = 6371000.0


@lru_cache(maxsize=128)
def get_utm_crs(lon: float, lat: float) -> pyproj.CRS:
    """
    Return the appropriate UTM CRS for the given longitude/latitude.
    Uses pyproj's automatic UTM zone detection.
    """
    utm_info_list = pyproj.database.query_utm_crs_info(
        area_of_interest=pyproj.aoi.AreaOfInterest(
            west_lon_degree=lon,
            south_lat_degree=lat,
            east_lon_degree=lon,
            north_lat_degree=lat,
        ),
        datum_name="WGS 84",
    )
    if utm_info_list:
        return pyproj.CRS.from_user_input(utm_info_list[0].code)

    # Fallback: compute UTM zone manually if query fails
    zone = int((lon + 180) / 6) + 1
    is_north = lat >= 0
    epsg = 32600 + zone if is_north else 32700 + zone
    return pyproj.CRS(f"EPSG:{epsg}")


@lru_cache(maxsize=128)
def meridian_convergence(lon: float, lat: float) -> float:
    """Grid convergence at a point, in degrees: ``true = grid + convergence``.

    A UTM grid squares up with its central meridian, not with true north, so a
    line drawn at 90° in the projected plane is tilted by the convergence angle
    once it is back in lon/lat and drawn on the map. Away from the central
    meridian that is ~1-2°, enough to make an orientation the user typed look
    slightly off. Callers add this back when converting a map bearing into a
    projected-plane angle.
    """
    try:
        proj = pyproj.Proj(get_utm_crs(lon, lat))
        return float(proj.get_factors(lon, lat).meridian_convergence)
    except Exception:
        return 0.0


def transform_coords(
    coords: Union[List[Any], Tuple[Any, ...]],
    transformer: pyproj.Transformer = None,
) -> Union[List[Any], Tuple[float, float], None]:
    """
    Recursively transform GeoJSON-style coordinates from WGS84 (lon/lat)
    to a local UTM projection (x/y in meters).

    Expected input shapes:
      - [lon, lat]                 -> (x, y)
      - [[lon, lat], ...]          -> [(x, y), ...]
      - Nested lists (MultiPolygon, etc.)

    The transformer is built once (either passed in or derived from the
    first coordinate) and reused for every point in the geometry to avoid
    expensive per-point CRS/transformer construction.

    Returns None for invalid/non-list elements instead of raising.
    """
    # Must be list/tuple
    if not isinstance(coords, (list, tuple)):
        return None

    # Base case: [lon, lat] or [lon, lat, alt]
    if (
        len(coords) >= 2
        and all(isinstance(c, (int, float)) for c in coords[:2])
    ):
        lon, lat = coords[0], coords[1]
        try:
            if transformer is None:
                utm = get_utm_crs(lon, lat)
                transformer = pyproj.Transformer.from_crs(
                    wgs84, utm, always_xy=True
                )
            x, y = transformer.transform(lon, lat)
            return (x, y)
        except Exception:
            # If projection fails for a single point, skip it
            return None

    if transformer is None:
        # Derive the UTM zone from the first coordinate leaf in the structure.
        first = coords
        while isinstance(first, (list, tuple)):
            if first and isinstance(first[0], (int, float)):
                break
            if not first or not isinstance(first[0], (list, tuple)):
                break
            first = first[0]
        if first and isinstance(first[0], (int, float)):
            utm = get_utm_crs(float(first[0]), float(first[1]))
            transformer = pyproj.Transformer.from_crs(
                wgs84, utm, always_xy=True
            )

    # Recursive case: list of coordinates
    result: List[Any] = []
    for c in coords:
        projected = transform_coords(c, transformer)
        if projected is not None:
            result.append(projected)

    return result if result else None


def project_geometry(
    geom_geojson: Dict[str, Any], lng: float, lat: float
) -> Tuple[Any, pyproj.CRS]:
    """
    Project a GeoJSON geometry dict to a local UTM CRS.

    Parameters
    ----------
    geom_geojson : dict
        GeoJSON-like dict with 'type' and 'coordinates'.
    lng, lat : float
        Approximate center of the geometry, used to pick the UTM zone.

    Returns
    -------
    (shapely_shape, utm_crs)
    """
    if not geom_geojson or "coordinates" not in geom_geojson:
        raise ValueError("Invalid GeoJSON: missing 'coordinates'")

    # One CRS for the whole project: derive it from the supplied centre and use
    # that same transformer for every vertex. Deriving the zone per point made
    # geometry that touches a UTM zone boundary (e.g. a vertex exactly at
    # 36.0°E, which pyproj puts in zone 36 while the centre 36.0006°E lands in
    # zone 37) come back 6° / ~500 km away when it was unprojected again.
    utm_crs = get_utm_crs(lng, lat)
    transformer = pyproj.Transformer.from_crs(wgs84, utm_crs, always_xy=True)
    projected = transform_coords(geom_geojson["coordinates"], transformer)
    if not projected:
        raise ValueError("Projected coordinates are empty – check input geometry")

    projected_geom = {
        "type": geom_geojson["type"],
        "coordinates": projected,
    }

    return shape(projected_geom), utm_crs


def projected_shape(
    polygon: Dict[str, Any], lng: float, lat: float, return_crs: bool = False
) -> Any:
    """
    Convenience wrapper: project a polygon GeoJSON.
    Returns shapely shape by default; if return_crs is True, returns (shape, crs).
    """
    shape_obj, crs = project_geometry(polygon, lng, lat)
    if return_crs:
        return shape_obj, crs
    return shape_obj


def unproject_shape(
    projected_shape_obj: Any, lng_or_crs: Any, lat: Any = None
) -> Dict[str, Any]:
    """
    Convert a projected Shapely shape (in UTM meters) back to a GeoJSON-like
    dict in WGS84 (lon/lat).

    Accepts either ``unproject_shape(shape, utm_crs)`` or the
    ``unproject_shape(shape, lng, lat)`` form; the latter derives the UTM CRS
    from the given center.

    Parameters
    ----------
    projected_shape_obj : shapely.geometry
        Shape in UTM coordinates.
    lng_or_crs : float | pyproj.CRS
        Center longitude, or the UTM CRS of the shape.
    lat : float, optional
        Center latitude (when lng_or_crs is a longitude).

    Returns
    -------
    dict
        GeoJSON-like dict with 'type' and 'coordinates' in WGS84.
    """
    if lat is None and isinstance(lng_or_crs, pyproj.CRS):
        utm_crs = lng_or_crs
    elif lat is not None:
        utm_crs = get_utm_crs(float(lng_or_crs), float(lat))
    else:
        raise ValueError(
            "unproject_shape requires either a UTM CRS or (lng, lat)"
        )

    transformer = pyproj.Transformer.from_crs(utm_crs, wgs84, always_xy=True)

    def transform_back(coords):
        if not isinstance(coords, (list, tuple)):
            return None
        # Base case: (x, y) or (x, y, z)
        if (
            len(coords) >= 2
            and all(isinstance(c, (int, float)) for c in coords[:2])
        ):
            x, y = coords[0], coords[1]
            try:
                lon, lat = transformer.transform(x, y)
                return (lon, lat)
            except Exception:
                return None
        # Recursive
        result = []
        for c in coords:
            unproj = transform_back(c)
            if unproj is not None:
                result.append(unproj)
        return result if result else None

    proj_coords = mapping(projected_shape_obj)["coordinates"]
    wgs_coords = transform_back(proj_coords)
    if not wgs_coords:
        raise ValueError("Failed to unproject shape: resulting coordinates are empty")

    return {
        "type": projected_shape_obj.geom_type,
        "coordinates": wgs_coords,
    }


def get_project_centroid(project_or_sector) -> Tuple[float, float]:
    """
    Compute the centroid (lon, lat) in WGS84 of a project or sector.

    Accepts:
      - A dict with a 'geometry' field containing a GeoJSON-like dict, or
      - An ORM model with a .geometry attribute that is GeoJSON-like:
          {"type": "Polygon"|"MultiPolygon", "coordinates": ...}

    Returns (lon, lat) as floats.
    """
    # Extract GeoJSON geometry
    if isinstance(project_or_sector, dict):
        geom = (
            project_or_sector.get("geometry")
            or project_or_sector.get("boundary")
            or project_or_sector.get("polygon")
        )
    else:
        # Assume ORM object with attributes
        geom = (
            getattr(project_or_sector, "geometry", None)
            or getattr(project_or_sector, "boundary", None)
            or getattr(project_or_sector, "polygon", None)
        )

    if not geom:
        raise ValueError("No geometry found on project/sector object")

    # Convert to Shapely shape (WGS84 coordinates)
    shapely_geom = shape(geom)

    # Compute centroid
    centroid = shapely_geom.centroid
    return centroid.x, centroid.y  # lon, lat in degrees
def haversine_length_meters(coords: List[Tuple[float, float]]) -> float:
    """
    Compute the total length in meters of a polyline defined by a list of
    (lon, lat) points using the haversine formula.

    Parameters
    ----------
    coords : list of (lon, lat)
        Sequence of longitude/latitude points in degrees.

    Returns
    -------
    float
        Total length in meters.
    """
    if len(coords) < 2:
        return 0.0

    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
        lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        total += EARTH_RADIUS_METERS * c

    return total