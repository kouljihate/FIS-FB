"""Components blueprint.

The Components section groups the fittings between the pipe network and the
field: valves, connectors, reductors, air removal, pressure reduction and
terminators.  It used to be a dropdown inside the hydrology blueprint, reached
through ``hydrology.*`` endpoints with no URL of its own.  It is now a
first-class tab like Trees, with its own URL space
(``/components/<project_id>/...``) and one sub-tab per component kind.
"""

from __future__ import annotations

import json
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from irrigation.components.valve_generator import VALVE_SHORT_PREFIXES, generate_valves
from irrigation.extensions import mongo

components_bp = Blueprint("components", __name__, url_prefix="/components")

#: Network element types that the Valves page owns.
VALVE_TYPES = ["master_valve", "sector_valve", "zone_valve"]

VALVE_NAMES = {
    "master_valve": ("Master Valve", "الصمام الرئيسي"),
    "sector_valve": ("Sector Valve", "صمام القطاع"),
    "zone_valve": ("Zone Valve", "صمام المنطقة"),
}

#: Short type label and badge colour shown in the valves table.
VALVE_TYPE_BADGES = {
    "master_valve": ("Master", "رئيسي", "danger"),
    "sector_valve": ("Sector", "قطاع", "warning"),
    "zone_valve": ("Zone", "منطقة", "info"),
}

# Short names: VM1 = Valve Master 1, VS1 = Valve Sector 1, VZ1 = Valve Zone 1.
# The prefixes live in the generator so both sides always agree.
def valve_short_name(valve_type, number):
    """``master_valve``/3 -> ``VM3``."""
    return "%s%d" % (VALVE_SHORT_PREFIXES.get(valve_type, "V"), number)


def _trailing_number(text):
    match = re.search(r"(\d+)\s*$", text or "")
    return int(match.group(1)) if match else None


def _next_valve_number(project_id, valve_type):
    """Number above every valve of this type already in the project.

    Counting the valves would reuse a number after a deletion (VS1, VS2 ->
    delete VS1 -> the next valve is VS2 again), so the highest short name / name
    number wins instead.
    """
    numbers = [
        _trailing_number((doc.get("properties") or {}).get("short_name"))
        or _trailing_number(doc.get("name_en"))
        or 0
        for doc in mongo.db.network_elements.find(
            {"project_id": project_id, "type": valve_type},
            {"properties.short_name": 1, "name_en": 1},
        )
    ]
    return (max(numbers) if numbers else 0) + 1


#: Sub-tabs that do not have their own grid yet, in navigation order.
PLACEHOLDERS = {
    "connectors": ("Connectors / الوصلات", "bi-link-45deg"),
    "reductors": ("Reductors / المخفضات", "bi-arrow-left-right"),
    "air_removal": ("Air Removal / إزالة الهواء", "bi-wind"),
    "pressure_reduction": ("Reduce Pressure / خفض الضغط", "bi-arrow-down-circle"),
    "terminators": ("Terminators / النهايات", "bi-stop-circle"),
}


def get_project_or_redirect(project_id):
    """Project owned by the signed-in user, or ``None`` after a flash."""
    project = mongo.db.projects.find_one(
        {"id": project_id, "user_id": int(current_user.id)}
    )
    if not project:
        flash("Project not found / المشروع غير موجود", "danger")
        return None
    project["_id"] = str(project["_id"])
    return project


def add_network_element(project_id, elem_type, name_en, name_ar, geometry, properties=None):
    from irrigation.models import get_next_id

    element = {
        "id": get_next_id("network_elements"),
        "project_id": project_id,
        "type": elem_type,
        "name_en": name_en,
        "name_ar": name_ar,
        "geometry": geometry,
        "properties": properties or {},
        "validated": False,
    }
    mongo.db.network_elements.insert_one(element)
    return element


def _invalidate_piping_report(project_id):
    """The AI piping snapshot no longer matches a hand-edited network."""
    mongo.db.piping_reports.delete_many({"project_id": project_id})


def _remove_elements(project_id, allowed_types):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))

    ids = [
        int(value)
        for value in request.form.getlist("element_ids")
        if str(value).strip().lstrip("-").isdigit()
    ]
    if not ids:
        flash("Select at least one element first / اختر عنصرا واحدا على الأقل أولا", "warning")
        return redirect(url_for("components.valves", project_id=project_id))

    query = {"project_id": project_id, "id": {"$in": ids}}
    if allowed_types:
        query["type"] = {"$in": allowed_types}
    result = mongo.db.network_elements.delete_many(query)
    if result.deleted_count:
        _invalidate_piping_report(project_id)
    flash(
        "Removed %d element(s) / تمت إزالة %d عنصر"
        % (result.deleted_count, result.deleted_count),
        "success",
    )
    return redirect(url_for("components.valves", project_id=project_id))


# === VALVES ==================================================================


@components_bp.route("/<int:project_id>/valves")
@login_required
def valves(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))

    sectors = list(mongo.db.sectors.find({"project_id": project_id}))
    zones = list(mongo.db.zones.find({"project_id": project_id}))
    for item in sectors + zones:
        item["_id"] = str(item["_id"])

    sector_names = {sector["id"]: sector for sector in sectors}
    zone_names = {zone["id"]: zone for zone in zones}

    valves = []
    type_counters: dict = {}
    for valve in mongo.db.network_elements.find(
        {"project_id": project_id, "type": {"$in": VALVE_TYPES}}
    ).sort("id", 1):
        valve["_id"] = str(valve["_id"])
        valve_type = valve.get("type")
        type_counters[valve_type] = type_counters.get(valve_type, 0) + 1
        label_en, label_ar, badge = VALVE_TYPE_BADGES.get(
            valve.get("type"), ("Valve", "صمام", "secondary")
        )
        valve["type_label_en"] = label_en
        valve["type_label_ar"] = label_ar
        valve["type_badge"] = badge

        properties = valve.get("properties") or {}
        # Short name: stored when the valve was created, otherwise derived from the
        # type and the number already in the name (older valves have neither).
        number = _trailing_number(valve.get("name_en")) or type_counters[valve_type]
        valve["short_name"] = properties.get("short_name") or valve_short_name(valve_type, number)
        valve["diameter"] = properties.get("diameter")
        valve["upstream_diameter"] = properties.get("upstream_diameter")
        valve["upstream_type"] = properties.get("upstream_type") or properties.get("upstream")
        valve["coordinates"] = (valve.get("geometry") or {}).get("coordinates")
        if valve.get("type") == "sector_valve":
            sector = sector_names.get(properties.get("sector_id"))
            valve["assignment_en"] = sector["name_en"] if sector else "Sector %s" % properties.get("sector_id", "-")
            valve["assignment_ar"] = sector["name_ar"] if sector else ""
            valve["assignment_kind_en"] = "Sector"
            valve["assignment_kind_ar"] = "قطاع"
        elif valve.get("type") == "zone_valve":
            zone = zone_names.get(properties.get("zone_id"))
            valve["assignment_en"] = zone["name_en"] if zone else "Zone %s" % properties.get("zone_id", "-")
            valve["assignment_ar"] = zone["name_ar"] if zone else ""
            valve["assignment_kind_en"] = "Zone"
            valve["assignment_kind_ar"] = "منطقة"
        else:
            valve["assignment_en"] = "Basin \u2192 Main pipe"
            valve["assignment_ar"] = "الحوض \u2192 الأنبوب الرئيسي"
            valve["assignment_kind_en"] = "Network"
            valve["assignment_kind_ar"] = "الشبكة"
        valves.append(valve)

    # Master valves first, then sector valves, then zone valves (ids ascending).
    order = {valve_type: index for index, valve_type in enumerate(VALVE_TYPES)}
    valves.sort(key=lambda item: (order.get(item.get("type"), len(order)), item.get("id", 0)))

    return render_template(
        "components/valves.html",
        project=project,
        sectors=sectors,
        zones=zones,
        valves=valves,
        valve_type_labels=VALVE_NAMES,
    )


@components_bp.route("/<int:project_id>/valves/add", methods=["POST"])
@login_required
def valve_add(project_id):
    valve_type = request.form.get("valve_type", "")
    coords = request.form.get("coordinates", "").strip()
    sector_id = request.form.get("sector_id", type=int)
    zone_id = request.form.get("zone_id", type=int)
    # Only the assignment that belongs to the chosen type is kept.
    if valve_type != "sector_valve":
        sector_id = None
    if valve_type != "zone_valve":
        zone_id = None

    if not coords or not valve_type:
        flash("Valve type and location required / نوع وموقع الصمام مطلوبان", "danger")
        return redirect(url_for("components.valves", project_id=project_id))

    try:
        coord = json.loads(coords)
        if isinstance(coord, list) and len(coord) == 2:
            geometry = {"type": "Point", "coordinates": coord}
        else:
            flash("Invalid coordinates / إحداثيات غير صالحة", "danger")
            return redirect(url_for("components.valves", project_id=project_id))
    except Exception:
        flash("Invalid coordinates / إحداثيات غير صالحة", "danger")
        return redirect(url_for("components.valves", project_id=project_id))

    if valve_type not in VALVE_NAMES:
        flash("Invalid valve type / نوع صمام غير صالح", "danger")
        return redirect(url_for("components.valves", project_id=project_id))

    number = _next_valve_number(project_id, valve_type)
    name_en, name_ar = VALVE_NAMES[valve_type]
    diameter = request.form.get("diameter", type=int)
    properties = {"source": "manual",
                  "short_name": valve_short_name(valve_type, number)}
    if sector_id:
        properties["sector_id"] = sector_id
    if zone_id:
        properties["zone_id"] = zone_id
    if diameter:
        properties["diameter"] = diameter

    add_network_element(
        project_id,
        valve_type,
        f"{name_en} {number}",
        f"{name_ar} {number}",
        geometry,
        properties,
    )
    _invalidate_piping_report(project_id)

    flash(f"{name_en} added / تمت إضافة {name_ar}", "success")
    return redirect(url_for("components.valves", project_id=project_id))


@components_bp.route("/<int:project_id>/valves/<int:element_id>/edit", methods=["POST"])
@login_required
def valve_edit(project_id, element_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))

    valve = mongo.db.network_elements.find_one(
        {"project_id": project_id, "id": element_id, "type": {"$in": VALVE_TYPES}}
    )
    if not valve:
        flash("Valve not found / الصمام غير موجود", "danger")
        return redirect(url_for("components.valves", project_id=project_id))

    valve_type = request.form.get("valve_type", valve.get("type"))
    if valve_type not in VALVE_NAMES:
        flash("Invalid valve type / نوع صمام غير صالح", "danger")
        return redirect(url_for("components.valves", project_id=project_id))

    name_en = request.form.get("name_en", "").strip()
    name_ar = request.form.get("name_ar", "").strip()
    if not name_en and not name_ar:
        name_en, name_ar = valve.get("name_en", ""), valve.get("name_ar", "")

    updates = {
        "type": valve_type,
        "name_en": name_en,
        "name_ar": name_ar,
    }

    properties = dict(valve.get("properties") or {})
    # Assignment follows the valve type: sector valves keep a sector, zone valves
    # keep a zone, the master valve is tied to the water source.
    properties.pop("sector_id", None)
    properties.pop("zone_id", None)
    if valve_type == "sector_valve":
        sector_id = request.form.get("sector_id", type=int)
        if sector_id:
            properties["sector_id"] = sector_id
    elif valve_type == "zone_valve":
        zone_id = request.form.get("zone_id", type=int)
        if zone_id:
            properties["zone_id"] = zone_id

    diameter = request.form.get("diameter", type=int)
    if diameter:
        properties["diameter"] = diameter
    # Keep the short name in step with the type: the number is preserved, the
    # prefix follows the valve kind (VM / VS / VZ).
    number = _trailing_number(properties.get("short_name")) or _trailing_number(name_en) \
        or mongo.db.network_elements.count_documents(
            {"project_id": project_id, "type": valve_type})
    properties["short_name"] = valve_short_name(valve_type, number)
    updates["properties"] = properties

    coords = request.form.get("coordinates", "").strip()
    if coords:
        try:
            coord = json.loads(coords)
            if isinstance(coord, list) and len(coord) == 2:
                updates["geometry"] = {"type": "Point", "coordinates": coord}
            else:
                flash("Invalid coordinates / إحداثيات غير صالحة", "danger")
                return redirect(url_for("components.valves", project_id=project_id))
        except Exception:
            flash("Invalid coordinates / إحداثيات غير صالحة", "danger")
            return redirect(url_for("components.valves", project_id=project_id))

    mongo.db.network_elements.update_one(
        {"project_id": project_id, "id": element_id}, {"$set": updates}
    )
    _invalidate_piping_report(project_id)
    flash("Valve updated / تم تحديث الصمام", "success")
    return redirect(url_for("components.valves", project_id=project_id))


@components_bp.route("/<int:project_id>/valves/generate", methods=["POST"])
@login_required
def valves_generate(project_id):
    """Place master / sector / zone valves from the drawn pipe network."""
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))

    replace = request.form.get("replace") in ("1", "on", "true")
    result = generate_valves(project_id, replace=replace)
    created = result.get("created") or {}
    total = sum(created.values())

    if total:
        flash(
            "Generated %d master, %d sector and %d zone valve(s) from the pipes / "
            "تم إنشاء %d صمام رئيسي و%d صمام قطاع و%d صمام منطقة"
            % (created.get("master_valve", 0), created.get("sector_valve", 0),
               created.get("zone_valve", 0), created.get("master_valve", 0),
               created.get("sector_valve", 0), created.get("zone_valve", 0)),
            "success",
        )
    else:
        flash(
            "No valve generated — every valve this network needs already exists or the pipes "
            "are missing / لم يتم إنشاء صمامات",
            "warning",
        )
    for warning in (result.get("warnings") or [])[:4]:
        flash(warning, "warning")
    return redirect(url_for("components.valves", project_id=project_id))


@components_bp.route("/<int:project_id>/valves/<int:element_id>/rename", methods=["POST"])
@login_required
def valve_rename(project_id, element_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))

    name_en = request.form.get("name_en", "").strip()
    name_ar = request.form.get("name_ar", "").strip()
    if not name_en and not name_ar:
        flash("A valve name is required / اسم الصمام مطلوب", "danger")
        return redirect(url_for("components.valves", project_id=project_id))

    result = mongo.db.network_elements.update_one(
        {"project_id": project_id, "id": element_id, "type": {"$in": VALVE_TYPES}},
        {"$set": {"name_en": name_en or name_ar, "name_ar": name_ar}},
    )
    if result.matched_count:
        flash("Valve renamed / تمت إعادة تسمية الصمام", "success")
    else:
        flash("Valve not found / الصمام غير موجود", "danger")
    return redirect(url_for("components.valves", project_id=project_id))


@components_bp.route("/<int:project_id>/valves/validate", methods=["POST"])
@login_required
def valves_validate(project_id):
    mongo.db.network_elements.update_many(
        {"project_id": project_id, "type": {"$in": VALVE_TYPES}},
        {"$set": {"validated": True}},
    )
    flash("Valves validated / تم التحقق من الصمامات", "success")
    return redirect(url_for("components.valves", project_id=project_id))


@components_bp.route("/<int:project_id>/valves/remove", methods=["POST"])
@login_required
def valves_remove(project_id):
    return _remove_elements(project_id, VALVE_TYPES)


# === PLACEHOLDER SUB-TABS ====================================================


def _placeholder(project_id, key):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))
    title, icon = PLACEHOLDERS[key]
    flash(f"{title.split(' / ')[0]} page coming soon", "info")
    return render_template(
        "components/placeholder.html", project=project, title=title, icon=icon
    )


@components_bp.route("/<int:project_id>/connectors")
@login_required
def connectors(project_id):
    return _placeholder(project_id, "connectors")


@components_bp.route("/<int:project_id>/reductors")
@login_required
def reductors(project_id):
    return _placeholder(project_id, "reductors")


@components_bp.route("/<int:project_id>/air_removal")
@login_required
def air_removal(project_id):
    return _placeholder(project_id, "air_removal")


@components_bp.route("/<int:project_id>/pressure_reduction")
@login_required
def pressure_reduction(project_id):
    return _placeholder(project_id, "pressure_reduction")


@components_bp.route("/<int:project_id>/terminators")
@login_required
def terminators(project_id):
    return _placeholder(project_id, "terminators")
