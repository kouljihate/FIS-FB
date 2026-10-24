"""Generate the fittings of the Components tabs from the drawn pipe network.

Every planner works on the pipes projected to metres, so a generated item sits
exactly on the pipe it belongs to:

* **Connectors** - a coupling where two pipes of the same diameter meet end to
  end, a tee where a pipe starts on the run of another pipe of the same
  diameter, and an elbow at a bend of 60 degrees or more.
* **Reductors** - a reduction at every joint that brings a narrower pipe onto a
  wider one; the wider pipe is stored as the upstream side.
* **Air Removal** - an air valve at the high end of the main pipe and of every
  second pipe, read from the project's terrain model.  Without a model the
  valve goes at the far junction of the main pipe, where trapped air collects.
* **Reduce Pressure** - one regulator at the head of each zone pipe, sized to
  that pipe and set to the pressure the drippers need.
* **Terminators** - an end cap on every free pipe end, i.e. a pipe end that
  reaches no other pipe and is not the water source.

Regeneration only replaces the items this module created (``generated: auto``);
hand-placed items and items from the network generator are never deleted, and a
new item is skipped when one already sits on the same point.
"""

from __future__ import annotations

import math
import re
from typing import Any

from shapely.geometry import Point

from irrigation.components.valve_generator import (
    _ends,
    _load_pipes,
    _nearest_element,
    _point_geojson,
    _snap_onto,
    _union,
)
from irrigation.engineering.elevation import get_elevation_at_point
from irrigation.engineering.projection import get_project_centroid, project_geometry
from irrigation.extensions import mongo
from irrigation.models import get_next_id

#: A pipe end this close to another pipe is treated as joined to it.
JOIN_TOLERANCE_M = 5.0
#: A bend this sharp gets an elbow.
BEND_ANGLE_DEG = 60.0
#: Rise needed before the end of a pipe counts as a high point for air.
MIN_AIR_RISE_M = 1.0
#: Standard 1" air release valve.
AIR_VALVE_DIAMETER_MM = 25
#: Outlet of the pressure regulator: the drippers' working pressure.
PRESSURE_SETPOINT_BAR = 1.5
#: An existing item this close to a planned point already covers it.
DUPLICATE_TOLERANCE_M = 3.0

PIPE_TYPES = ("main_pipe", "second_pipe", "sub_pipe")


# ---------------------------------------------------------------------------
# network context
# ---------------------------------------------------------------------------

def _context(project_id: int) -> dict | None:
    """Everything the planners read, with the pipes already projected."""
    project = mongo.db.projects.find_one({"id": project_id})
    if not project:
        return None

    lng, lat = get_project_centroid(project)
    crs_holder: dict = {}
    groups = _load_pipes(project_id, lng, lat, crs_holder)
    pipes = [pipe for pipe_type in PIPE_TYPES for pipe in groups.get(pipe_type, [])]

    source = None
    water_source = project.get("water_source")
    if isinstance(water_source, dict) and water_source.get("coordinates"):
        try:
            source = project_geometry(water_source, lng, lat)[0]
        except Exception:
            source = None

    if source is None:
        try:
            source = project_geometry(
                {"type": "Point", "coordinates": [lng, lat]}, lng, lat
            )[0]
        except Exception:
            source = None

    return {
        "project": project,
        "lng": lng,
        "lat": lat,
        "crs": crs_holder.get("crs"),
        "groups": groups,
        "pipes": pipes,
        "source": source,
        "joints": _joints(pipes),
    }


def _pipe_key(pipe) -> Any:
    return pipe.id if pipe.id is not None else id(pipe)


def _round_point(point: Point) -> tuple[float, float]:
    return (round(point.x, 1), round(point.y, 1))


def _same_diameter(first, second) -> bool:
    """Unknown diameters are treated as matching, so a plain fitting is used."""
    if first.diameter is None or second.diameter is None:
        return True
    return int(first.diameter) == int(second.diameter)


def _joints(pipes: list) -> list[dict]:
    """Every place where one pipe end reaches another pipe.

    ``kind`` is ``inline`` when the two pipes meet end to end (a coupling or a
    reduction) and ``branch`` when the pipe starts on the run of the other one
    (a tee).
    """
    joints: list[dict] = []
    seen: set = set()
    for pipe in pipes:
        ends = _ends(pipe.shape)
        if not ends:
            continue
        for point in ends:
            best = None
            for other in pipes:
                if other is pipe:
                    continue
                distance = other.shape.distance(point)
                if best is None or distance < best[0]:
                    best = (distance, other)
            if best is None or best[0] > JOIN_TOLERANCE_M:
                continue
            distance, other = best

            inline = False
            other_ends = _ends(other.shape)
            if other_ends:
                inline = min(point.distance(end) for end in other_ends) <= JOIN_TOLERANCE_M

            if inline:
                key = ("inline",) + tuple(sorted((_pipe_key(pipe), _pipe_key(other))))
            else:
                key = ("branch", _pipe_key(pipe), _pipe_key(other), _round_point(point))
            if key in seen:
                continue
            seen.add(key)
            if distance > 1e-9:
                # Draw the item on the pipe it joins, not on the gap between.
                point, _closed = _snap_onto(other.shape, point)
            joints.append({
                "pipe": pipe,
                "other": other,
                "point": point,
                "kind": "inline" if inline else "branch",
                "gap": distance,
            })
    return joints


def _bend_points(pipe) -> list[Point]:
    """Vertices where the pipe turns by ``BEND_ANGLE_DEG`` or more."""
    shape = pipe.shape
    if shape is None or shape.is_empty:
        return []
    lines = [shape] if shape.geom_type == "LineString" else list(getattr(shape, "geoms", []))
    points: list[Point] = []
    for line in lines:
        coords = list(line.coords)
        for index in range(1, len(coords) - 1):
            first, corner, last = coords[index - 1], coords[index], coords[index + 1]
            incoming = (corner[0] - first[0], corner[1] - first[1])
            outgoing = (last[0] - corner[0], last[1] - corner[1])
            length_in = (incoming[0] ** 2 + incoming[1] ** 2) ** 0.5
            length_out = (outgoing[0] ** 2 + outgoing[1] ** 2) ** 0.5
            if not length_in or not length_out:
                continue
            cosine = (incoming[0] * outgoing[0] + incoming[1] * outgoing[1]) / (length_in * length_out)
            cosine = max(-1.0, min(1.0, cosine))
            if math.degrees(math.acos(cosine)) >= BEND_ANGLE_DEG:
                points.append(Point(corner))
    return points


# ---------------------------------------------------------------------------
# planners: each returns (plans, warnings) with plans = [(Point, properties)]
# ---------------------------------------------------------------------------

def _connector_plans(context: dict) -> tuple[list, list[str]]:
    plans: list[tuple[Point, dict]] = []
    joints = context["joints"]

    for joint in joints:
        pipe, other = joint["pipe"], joint["other"]
        if not _same_diameter(pipe, other):
            continue  # a diameter change belongs to the Reductors tab
        properties = {
            "connector_type": "coupling" if joint["kind"] == "inline" else "tee",
            "joint": joint["kind"],
            "diameter": pipe.diameter,
            "pipe_id": pipe.id,
            "pipe_type": pipe.type,
            "other_pipe_id": other.id,
            "other_pipe_type": other.type,
        }
        plans.append((joint["point"], properties))

    joint_points = [joint["point"] for joint in joints]
    for pipe in context["pipes"]:
        for bend in _bend_points(pipe):
            if any(bend.distance(point) <= JOIN_TOLERANCE_M for point in joint_points):
                continue
            plans.append((bend, {
                "connector_type": "elbow",
                "joint": "bend",
                "diameter": pipe.diameter,
                "pipe_id": pipe.id,
                "pipe_type": pipe.type,
            }))

    warnings: list[str] = []
    if not plans:
        warnings.append(
            "No pipe joint or bend to fit — draw the pipes first / "
            "لا توجد وصلات أو انحناءات في الأنابيب المرسومة"
        )
    return plans, warnings


def _reductor_plans(context: dict) -> tuple[list, list[str]]:
    plans: list[tuple[Point, dict]] = []
    for joint in context["joints"]:
        pipe, other = joint["pipe"], joint["other"]
        if pipe.diameter is None or other.diameter is None:
            continue
        if int(pipe.diameter) == int(other.diameter):
            continue
        wide, narrow = (pipe, other) if pipe.diameter > other.diameter else (other, pipe)
        plans.append((joint["point"], {
            "from_diameter": int(wide.diameter),
            "to_diameter": int(narrow.diameter),
            "upstream_pipe_id": wide.id,
            "upstream_type": wide.type,
            "downstream_pipe_id": narrow.id,
            "downstream_type": narrow.type,
        }))

    warnings: list[str] = []
    if not plans:
        warnings.append(
            "Every pipe joint keeps the same diameter — no reductor needed / "
            "كل وصلات الأنابيب بنفس القطر — لا حاجة لمخفض"
        )
    return plans, warnings


def _air_plans(context: dict) -> tuple[list, list[str]]:
    plans: list[tuple[Point, dict]] = []
    warnings: list[str] = []
    groups = context["groups"]
    mains = groups.get("main_pipe", []) + groups.get("second_pipe", [])

    model = mongo.db.project_elevation_models.find_one(
        {"project_id": context["project"]["id"]}
    )

    if model and model.get("samples"):
        for pipe in mains:
            ends = _ends(pipe.shape)
            if not ends:
                continue
            first, second = ends
            first_z = get_elevation_at_point(model, first)
            second_z = get_elevation_at_point(model, second)
            if first_z is None or second_z is None:
                continue
            if abs(first_z - second_z) < MIN_AIR_RISE_M:
                continue
            high, high_z = (first, first_z) if first_z > second_z else (second, second_z)
            plans.append((high, {
                "node": "high_point",
                "diameter": AIR_VALVE_DIAMETER_MM,
                "elevation_m": round(high_z, 2),
                "pipe_id": pipe.id,
                "pipe_type": pipe.type,
            }))
        if not plans:
            warnings.append(
                "The mains do not rise %.0f m, so there is no air pocket / "
                "الأنابيب الرئيسية لا ترتفع %.0f م — لا يوجد جيب هواء"
                % (MIN_AIR_RISE_M, MIN_AIR_RISE_M)
            )
        return plans, warnings

    # No terrain model yet: air collects at the far end of the main pipe, and a
    # junction there stays free for the terminator that caps the pipe end.
    main_pipes = groups.get("main_pipe", [])
    if not main_pipes:
        warnings.append(
            "No main pipe (MP) — no air valve placed / لا يوجد أنبوب رئيسي"
        )
        return plans, warnings

    reference = context["source"]
    if reference is None:
        return plans, warnings

    junctions = [
        joint for joint in context["joints"]
        if joint["pipe"].type == "main_pipe" or joint["other"].type == "main_pipe"
    ]
    if junctions:
        joint = max(junctions, key=lambda item: item["point"].distance(reference))
        pipe = joint["pipe"] if joint["pipe"].type == "main_pipe" else joint["other"]
        plans.append((joint["point"], {
            "node": "main_pipe_junction",
            "diameter": AIR_VALVE_DIAMETER_MM,
            "pipe_id": pipe.id,
            "pipe_type": "main_pipe",
        }))
    else:
        ends: list[Point] = []
        for pipe in main_pipes:
            ends.extend(_ends(pipe.shape) or [])
        if ends:
            far = max(ends, key=lambda point: point.distance(reference))
            pipe = _nearest_element(main_pipes, far)
            plans.append((far, {
                "node": "main_pipe_end",
                "diameter": AIR_VALVE_DIAMETER_MM,
                "pipe_id": pipe.id if pipe else None,
                "pipe_type": "main_pipe",
            }))

    if plans:
        warnings.append(
            "No terrain model yet — the air valve is placed at the far end of the main pipe / "
            "لا يوجد نموذج تضاريس — وُضع صمام التنفيس في آخر الأنبوب الرئيسي"
        )
    return plans, warnings


def _pressure_plans(context: dict) -> tuple[list, list[str]]:
    plans: list[tuple[Point, dict]] = []
    warnings: list[str] = []
    groups = context["groups"]
    zone_pipes = groups.get("sub_pipe", [])
    if not zone_pipes:
        warnings.append(
            "No zone pipe (ZP) — no pressure reducer placed / لا يوجد أنبوب منطقة"
        )
        return plans, warnings

    upstream = _union([
        shape for shape in (
            _union(pipe.shape for pipe in groups.get("main_pipe", [])),
            _union(pipe.shape for pipe in groups.get("second_pipe", [])),
        ) if shape is not None
    ])

    # One regulator per zone, on the widest zone pipe that serves it.
    chosen: dict = {}
    for pipe in zone_pipes:
        ends = _ends(pipe.shape)
        if not ends:
            continue
        if upstream is not None:
            head = min(ends, key=lambda point: upstream.distance(point))
            point, _gap = _snap_onto(upstream, head)
        else:
            reference = context["source"]
            if reference is None:
                continue
            head = min(ends, key=lambda item: reference.distance(item))
            point, _gap = _snap_onto(pipe.shape, head)

        key = ("zone", pipe.zone_id) if pipe.zone_id is not None else ("pipe", pipe.id)
        current = chosen.get(key)
        if current is None or (pipe.diameter or 0) >= (current[1].diameter or 0):
            chosen[key] = (point, pipe)

    for point, pipe in chosen.values():
        properties = {
            "node": "zone_head",
            "diameter": pipe.diameter,
            "outlet_pressure": PRESSURE_SETPOINT_BAR,
            "pipe_id": pipe.id,
            "pipe_type": "sub_pipe",
        }
        if pipe.zone_id is not None:
            properties["zone_id"] = pipe.zone_id
        plans.append((point, properties))

    return plans, warnings


def _terminator_plans(context: dict) -> tuple[list, list[str]]:
    plans: list[tuple[Point, dict]] = []
    source = context["source"]
    for pipe in context["pipes"]:
        for point in _ends(pipe.shape) or []:
            if source is not None and point.distance(source) <= JOIN_TOLERANCE_M:
                continue  # the source is not a dead end
            if any(
                other is not pipe and other.shape.distance(point) <= JOIN_TOLERANCE_M
                for other in context["pipes"]
            ):
                continue  # the end reaches another pipe
            plans.append((point, {
                "node": "pipe_end",
                "diameter": pipe.diameter,
                "pipe_id": pipe.id,
                "pipe_type": pipe.type,
            }))

    warnings: list[str] = []
    if not plans:
        warnings.append(
            "Every pipe end already reaches another pipe — no terminator needed / "
            "كل أطراف الأنابيب متصلة — لا حاجة لسدادة"
        )
    return plans, warnings


PLANNERS = {
    "connectors": _connector_plans,
    "reductors": _reductor_plans,
    "air_removal": _air_plans,
    "pressure_reduction": _pressure_plans,
    "terminators": _terminator_plans,
}


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def _trailing_number(text: str | None) -> int | None:
    match = re.search(r"(\d+)\s*$", text or "")
    return int(match.group(1)) if match else None


def _first_number(text: str | None) -> int | None:
    match = re.search(r"(\d+)", text or "")
    return int(match.group(1)) if match else None


def _next_number(project_id: int, element_type: str) -> int:
    """Number above every item of this type already in the project."""
    highest = 0
    for doc in mongo.db.network_elements.find(
        {"project_id": project_id, "type": element_type},
        {"properties.short_name": 1, "name_en": 1},
    ):
        number = (
            _trailing_number((doc.get("properties") or {}).get("short_name"))
            or _trailing_number(doc.get("name_en"))
            or _first_number(doc.get("name_en"))
            or 0
        )
        highest = max(highest, number)
    return highest + 1


def _existing_points(project_id: int, element_type: str, lng: float, lat: float) -> list[Point]:
    points: list[Point] = []
    for doc in mongo.db.network_elements.find(
        {"project_id": project_id, "type": element_type}, {"geometry": 1}
    ):
        geometry = doc.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue
        try:
            points.append(project_geometry(geometry, lng, lat)[0])
        except Exception:
            continue
    return points


def _near(point: Point, points: list[Point], tolerance: float = DUPLICATE_TOLERANCE_M) -> bool:
    return any(point.distance(other) <= tolerance for other in points)


def generate_components(project_id: int, tab: dict, replace: bool = True) -> dict:
    """Place the tab's fittings on the project's pipes.

    ``tab`` is the entry of ``COMPONENT_TABS`` so the element type, prefix and
    labels stay in one place.  Returns ``{"created": n, "warnings": [...]}``.
    """
    slug = tab["slug"]
    element_type = tab["element_type"]
    planner = PLANNERS.get(slug)
    if planner is None:
        return {"created": 0, "warnings": ["Unknown component tab / تبويب غير معروف"]}

    context = _context(project_id)
    if context is None:
        return {"created": 0, "warnings": ["Project not found / المشروع غير موجود"]}
    if not context["pipes"]:
        return {
            "created": 0,
            "warnings": [
                "No pipes drawn yet — draw MP / SP / ZP first / "
                "لا توجد أنابيب مرسومة بعد — ارسم الأنابيب أولا"
            ],
        }

    plans, warnings = planner(context)

    if replace:
        mongo.db.network_elements.delete_many({
            "project_id": project_id,
            "type": element_type,
            "properties.generated": "auto",
        })

    existing = _existing_points(project_id, element_type, context["lng"], context["lat"])
    number = _next_number(project_id, element_type)
    documents: list[dict] = []
    placed: list[Point] = []
    for point, properties in plans:
        if _near(point, existing) or _near(point, placed):
            continue
        placed.append(point)
        properties = dict(properties)
        properties["generated"] = "auto"
        properties["short_name"] = "%s%d" % (tab["prefix"], number)
        documents.append({
            "id": get_next_id("network_elements"),
            "project_id": project_id,
            "type": element_type,
            "name_en": "%s %d" % (tab["item_en"], number),
            "name_ar": "%s %d" % (tab["item_ar"], number),
            "geometry": _point_geojson(point, context["crs"]),
            "properties": properties,
            "validated": False,
        })
        number += 1

    if documents:
        mongo.db.network_elements.insert_many(documents)
        mongo.db.piping_reports.delete_many({"project_id": project_id})
    elif plans:
        warnings.append(
            "Every item this network needs already exists / كل العناصر المطلوبة موجودة بالفعل"
        )

    return {"created": len(documents), "warnings": warnings}
