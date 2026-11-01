"""Generate master / sector / zone valves from the stored pipe network.

Every valve is placed on the pipe it actually switches and gets the diameter of
the pipe it serves:

* **Master valve** - where the main pipe (MP) meets the water source, diameter
  taken from the main pipe it sits on.
* **Sector valve** - where a sector's second pipe (SP) leaves the main pipe,
  diameter taken from that second pipe.
* **Zone valve** - where a zone's zone pipe (ZP) leaves its upstream pipe
  (second pipe when there is one, main pipe otherwise), diameter taken from the
  zone pipe.

Junctions come from snapping the pipe end onto the upstream pipe geometry, so a
gap between two drawn pipes still yields one exact point.  Pipes a valve needs
are reported as warnings instead of being invented.
"""

from __future__ import annotations

from typing import Any, Iterable

from shapely.geometry import Point
from shapely.ops import nearest_points

from irrigation.engineering.projection import (
    get_project_centroid,
    project_geometry,
    unproject_shape,
)
from irrigation.extensions import mongo
from irrigation.models import get_next_id

VALVE_TYPES = ("master_valve", "sector_valve", "zone_valve")
PIPE_TYPES = ("main_pipe", "second_pipe", "sub_pipe")

#: A pipe end this far from the pipe it should feed is still treated as connected.
SNAP_TOLERANCE_M = 30.0
#: A zone pipe is considered connected to a second pipe while it is this close.
SECOND_PIPE_TOLERANCE_M = 5.0

VALVE_LABELS = {
    "master_valve": ("Master Valve", "الصمام الرئيسي"),
    "sector_valve": ("Sector Valve", "صمام القطاع"),
    "zone_valve": ("Zone Valve", "صمام المنطقة"),
}

#: Master Valve 1 -> VM1, Sector Valve 1 -> VS1, Zone Valve 1 -> VZ1.
VALVE_SHORT_PREFIXES = {
    "master_valve": "VM",
    "sector_valve": "VS",
    "zone_valve": "VZ",
}


class _Pipe:
    """One stored pipe, projected to metres for geometric work."""

    __slots__ = ("doc", "shape", "diameter", "zone_id")

    def __init__(self, doc: dict, shape: Any):
        self.doc = doc
        self.shape = shape
        properties = doc.get("properties") or {}
        diameter = properties.get("diameter")
        self.diameter = int(diameter) if isinstance(diameter, (int, float)) else None
        self.zone_id = properties.get("zone_id")

    @property
    def id(self) -> Any:
        return self.doc.get("id")

    @property
    def type(self) -> str:
        return self.doc.get("type")


def _ends(shape: Any) -> tuple[Point, Point] | None:
    """The two extreme endpoints of a LineString / MultiLineString."""
    if shape is None or shape.is_empty:
        return None
    if shape.geom_type == "LineString":
        coords = list(shape.coords)
        return Point(coords[0]), Point(coords[-1])
    if shape.geom_type == "MultiLineString":
        points: list[Point] = []
        for part in shape.geoms:
            coords = list(part.coords)
            points.extend((Point(coords[0]), Point(coords[-1])))
        best = None
        for index, first in enumerate(points):
            for second in points[index + 1:]:
                distance = first.distance(second)
                if best is None or distance > best[0]:
                    best = (distance, first, second)
        if best is not None:
            return best[1], best[2]
    return None


def _union(shapes: Iterable[Any]) -> Any | None:
    merged = None
    for shape in shapes:
        if shape is None or shape.is_empty:
            continue
        merged = shape if merged is None else merged.union(shape)
    return merged


def _nearest_element(pipes: list[_Pipe], point: Point) -> _Pipe | None:
    best: tuple[float, _Pipe] | None = None
    for pipe in pipes:
        distance = pipe.shape.distance(point)
        if best is None or distance < best[0]:
            best = (distance, pipe)
    return best[1] if best else None


def _snap_onto(network: Any, point: Point) -> tuple[Point, float]:
    """Closest point on ``network`` to ``point`` plus the gap that was closed."""
    gap = network.distance(point)
    if gap == 0:
        return point, 0.0
    return nearest_points(network, point)[0], gap


def _point_geojson(point: Point, crs: Any) -> dict:
    geo = unproject_shape(point, crs)
    coordinates = [round(value, 7) for value in geo["coordinates"]]
    return {"type": "Point", "coordinates": coordinates}


def _load_pipes(project_id: int, lng: float, lat: float, crs_holder: dict) -> dict[str, list[_Pipe]]:
    pipes: dict[str, list[_Pipe]] = {pipe_type: [] for pipe_type in PIPE_TYPES}
    for doc in mongo.db.network_elements.find(
        {"project_id": project_id, "type": {"$in": list(PIPE_TYPES)}}
    ):
        geometry = doc.get("geometry") or {}
        if not geometry.get("coordinates"):
            continue
        try:
            shape, crs = project_geometry(geometry, lng, lat)
        except Exception:
            continue
        crs_holder.setdefault("crs", crs)
        pipes[doc.get("type")].append(_Pipe(doc, shape))
    return pipes


def _sector_of_point(sectors: list[dict], point: Point) -> tuple[dict | None, float]:
    """Containing sector, or the nearest one and how far away it is."""
    best: tuple[float, dict] | None = None
    for sector in sectors:
        shape = sector["shape"]
        if shape.contains(point) or shape.intersects(point):
            return sector, 0.0
        distance = shape.distance(point)
        if best is None or distance < best[0]:
            best = (distance, sector)
    return (best[1], best[0]) if best else (None, float("inf"))


def _zone_of_point(zones: list[dict], point: Point) -> tuple[dict | None, float]:
    return _sector_of_point(zones, point)


def _valve_doc(project_id: int, valve_type: str, index: int, point: Point, crs: Any,
               properties: dict) -> dict:
    name_en, name_ar = VALVE_LABELS[valve_type]
    properties = dict(properties)
    properties["short_name"] = "%s%d" % (VALVE_SHORT_PREFIXES.get(valve_type, "V"), index)
    return {
        "id": get_next_id("network_elements"),
        "project_id": project_id,
        "type": valve_type,
        "name_en": "%s %d" % (name_en, index),
        "name_ar": "%s %d" % (name_ar, index),
        "geometry": _point_geojson(point, crs),
        "properties": properties,
        "validated": False,
    }


def generate_valves(project_id: int, replace: bool = True) -> dict:
    """Create valves for the project's current pipes.

    Returns a summary with the counts created per valve type plus warnings about
    the pipes that are missing.
    """
    project = mongo.db.projects.find_one({"id": project_id})
    if not project:
        return {"created": {}, "warnings": ["Project not found"], "skipped": {}}

    lng, lat = get_project_centroid(project)
    crs_holder: dict = {}
    pipes = _load_pipes(project_id, lng, lat, crs_holder)
    crs = crs_holder.get("crs")

    sectors = []
    for doc in mongo.db.sectors.find({"project_id": project_id}):
        polygon = doc.get("polygon")
        if not polygon:
            continue
        try:
            sectors.append({"id": doc["id"], "name": doc.get("name_en") or doc.get("name_ar") or doc["id"],
                            "shape": project_geometry(polygon, lng, lat)[0]})
        except Exception:
            continue

    zones = []
    for doc in mongo.db.zones.find({"project_id": project_id}):
        polygon = doc.get("polygon")
        if not polygon:
            continue
        try:
            zones.append({"id": doc["id"], "sector_id": doc.get("sector_id"),
                          "name": doc.get("name_en") or doc.get("name_ar") or doc["id"],
                          "shape": project_geometry(polygon, lng, lat)[0]})
        except Exception:
            continue

    trunk = _union(pipe.shape for pipe in pipes["main_pipe"])
    second_net = _union(pipe.shape for pipe in pipes["second_pipe"])

    warnings: list[str] = []
    skipped: dict[str, list[str]] = {"sectors": [], "zones": []}
    plans: list[tuple[str, Point, dict]] = []

    # -- master valve: basin -> main pipe --------------------------------- #
    if trunk is None:
        warnings.append("No main pipe (MP) — no master valve placed / لا يوجد أنبوب رئيسي")
    else:
        source = project.get("water_source") or {}
        source_coords = source.get("coordinates") if isinstance(source, dict) else None
        if source_coords:
            try:
                source_point = project_geometry(source, lng, lat)[0]
                at_source = True
            except Exception:
                source_point, at_source = None, False
        else:
            source_point, at_source = None, False

        if source_point is None:
            # Fall back to the main pipe end closest to the project centre.
            centre = project_geometry({"type": "Point", "coordinates": [lng, lat]}, lng, lat)[0]
            best_pipe = _nearest_element(pipes["main_pipe"], centre)
            ends = _ends(best_pipe.shape) if best_pipe else None
            point = min(ends, key=lambda item: item.distance(centre)) if ends else None
            warnings.append("No water source recorded — master valve placed at the main pipe end "
                            "nearest the project centre / لا يوجد مصدر مياه مسجل")
        else:
            point, _ = _snap_onto(trunk, source_point)

        if point is not None:
            pipe = _nearest_element(pipes["main_pipe"], point)
            properties = {
                "generated": "auto",
                "node": "source" if at_source else "main_pipe_end",
                "diameter": pipe.diameter if pipe else None,
                "pipe_id": pipe.id if pipe else None,
                "pipe_type": "main_pipe",
                "upstream": "water_source",
            }
            plans.append(("master_valve", point, properties))

    # -- sector valves: main pipe -> second pipe --------------------------- #
    # A second pipe serves the sector of the zones its far end reaches (zones
    # carry the authoritative sector link); geometry is the fallback, and the
    # widest candidate wins when a sector has several.
    sector_pipes: dict[int, _Pipe] = {}
    for pipe in pipes["second_pipe"]:
        ends = _ends(pipe.shape)
        if not ends or trunk is None:
            continue
        first, second = ends
        _tap, service = ((first, second) if trunk.distance(first) <= trunk.distance(second)
                         else (second, first))

        sector = None
        zone, _distance = _zone_of_point(zones, service)
        if zone is not None and zone.get("sector_id") is not None:
            sector = next((item for item in sectors if item["id"] == zone["sector_id"]), None)
        if sector is None:
            sector, distance = _sector_of_point(sectors, service)
            if sector is not None and distance > SNAP_TOLERANCE_M:
                sector = None
        if sector is None:
            continue
        current = sector_pipes.get(sector["id"])
        if current is None or (pipe.diameter or 0) >= (current.diameter or 0):
            sector_pipes[sector["id"]] = pipe

    for sector in sectors:
        pipe = sector_pipes.get(sector["id"])
        if pipe is None:
            skipped["sectors"].append(str(sector["name"]))
            continue
        ends = _ends(pipe.shape)
        first, second = ends
        tap, _service = ((first, second) if trunk.distance(first) <= trunk.distance(second)
                         else (second, first))
        point, gap = _snap_onto(trunk, tap)
        upstream_pipe = _nearest_element(pipes["main_pipe"], point)
        if gap > SNAP_TOLERANCE_M:
            warnings.append("%s: second pipe starts %.0f m from the main pipe — valve snapped onto the "
                            "main pipe / الأنبوب الثانوي بعيد عن الأنبوب الرئيسي" % (sector["name"], gap))
        plans.append(("sector_valve", point, {
            "generated": "auto",
            "sector_id": sector["id"],
            "diameter": pipe.diameter,
            "upstream_diameter": upstream_pipe.diameter if upstream_pipe else None,
            "upstream_pipe_id": upstream_pipe.id if upstream_pipe else None,
            "upstream_type": "main_pipe",
            "pipe_type": "second_pipe",
            "pipe_id": pipe.id,
        }))

    # -- zone valves: second pipe (or main pipe) -> zone pipe -------------- #
    # A zone pipe is owned by the zone its far end reaches; ``properties.zone_id``
    # is the fallback when the drawn pipe stops short of the polygon.
    upstream_all = _union(shape for shape in (trunk, second_net) if shape is not None)
    upstream_for_zone: dict[int, _Pipe] = {}
    for pipe in pipes["sub_pipe"]:
        ends = _ends(pipe.shape)
        if not ends:
            continue
        first, second = ends

        # properties.zone_id is authoritative; the far end only identifies the
        # zone when the pipe was drawn without one.
        zone = None
        if pipe.zone_id is not None:
            zone = next((item for item in zones if item["id"] == pipe.zone_id), None)
        if zone is None and upstream_all is not None:
            _tap, service = ((first, second)
                             if upstream_all.distance(first) <= upstream_all.distance(second)
                             else (second, first))
            zone, distance = _zone_of_point(zones, service)
            if zone is not None and distance > SNAP_TOLERANCE_M:
                zone = None
        if zone is None:
            continue
        upstream_for_zone[zone["id"]] = pipe

    for zone in zones:
        pipe = upstream_for_zone.get(zone["id"])
        if pipe is None:
            skipped["zones"].append(str(zone["name"]))
            continue
        ends = _ends(pipe.shape)
        first, second = ends
        chosen_net, upstream_type = None, None
        d_second = second_net.distance(first) if second_net is not None else float("inf")
        d_second = min(d_second, second_net.distance(second)) if second_net is not None else float("inf")
        d_trunk = min(trunk.distance(first), trunk.distance(second)) if trunk is not None else float("inf")
        if second_net is not None and d_second <= SECOND_PIPE_TOLERANCE_M and d_second <= d_trunk + SECOND_PIPE_TOLERANCE_M:
            chosen_net, upstream_type = second_net, "second_pipe"
        elif trunk is not None:
            chosen_net, upstream_type = trunk, "main_pipe"

        if chosen_net is None:
            skipped["zones"].append(str(zone["name"]))
            continue

        tap, service = ((first, second) if chosen_net.distance(first) <= chosen_net.distance(second)
                        else (second, first))
        point, gap = _snap_onto(chosen_net, tap)
        upstream_pipe = _nearest_element(pipes[upstream_type], point)
        if gap > SNAP_TOLERANCE_M:
            warnings.append("%s: zone pipe starts %.0f m from its %s — valve snapped onto the pipe / "
                            "أنبوب المنطقة بعيد عن الأنبوب المغذي" % (zone["name"], gap, upstream_type.replace("_", " ")))
        plans.append(("zone_valve", point, {
            "generated": "auto",
            "zone_id": zone["id"],
            "sector_id": zone.get("sector_id"),
            "diameter": pipe.diameter,
            "upstream_diameter": upstream_pipe.diameter if upstream_pipe else None,
            "upstream_pipe_id": upstream_pipe.id if upstream_pipe else None,
            "upstream_type": upstream_type,
            "pipe_type": "sub_pipe",
            "pipe_id": pipe.id,
        }))

    # -- persist ---------------------------------------------------------- #
    existing: dict[str, list[dict]] = {valve_type: [] for valve_type in VALVE_TYPES}
    for doc in mongo.db.network_elements.find(
        {"project_id": project_id, "type": {"$in": list(VALVE_TYPES)}}
    ):
        existing.setdefault(doc.get("type"), []).append(doc)

    if replace:
        mongo.db.network_elements.delete_many(
            {"project_id": project_id, "type": {"$in": list(VALVE_TYPES)}}
        )
        existing = {valve_type: [] for valve_type in VALVE_TYPES}

    already = {
        "master_valve": len(existing.get("master_valve") or []) > 0,
        "sector_valve": {str((doc.get("properties") or {}).get("sector_id"))
                         for doc in existing.get("sector_valve") or []},
        "zone_valve": {str((doc.get("properties") or {}).get("zone_id"))
                       for doc in existing.get("zone_valve") or []},
    }

    counters = {valve_type: len(existing.get(valve_type) or []) for valve_type in VALVE_TYPES}
    created = {valve_type: 0 for valve_type in VALVE_TYPES}
    documents: list[dict] = []
    for valve_type, point, properties in plans:
        if not replace:
            if valve_type == "master_valve" and already["master_valve"]:
                continue
            if valve_type == "sector_valve" and str(properties.get("sector_id")) in already["sector_valve"]:
                continue
            if valve_type == "zone_valve" and str(properties.get("zone_id")) in already["zone_valve"]:
                continue
        counters[valve_type] += 1
        created[valve_type] += 1
        documents.append(_valve_doc(project_id, valve_type, counters[valve_type], point, crs, properties))

    if documents:
        mongo.db.network_elements.insert_many(documents)
        mongo.db.piping_reports.delete_many({"project_id": project_id})

    if skipped["sectors"]:
        warnings.append("%d sector(s) have no second pipe, so no sector valve: %s / قطاعات بلا أنبوب ثانوي"
                        % (len(skipped["sectors"]), ", ".join(skipped["sectors"][:6])))
    if skipped["zones"]:
        warnings.append("%d zone(s) have no zone pipe, so no zone valve: %s / مناطق بلا أنبوب منطقة"
                        % (len(skipped["zones"]), ", ".join(skipped["zones"][:6])))

    return {"created": created, "warnings": warnings, "skipped": skipped}
