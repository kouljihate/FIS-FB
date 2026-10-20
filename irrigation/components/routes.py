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


def _next_element_number(project_id, element_type):
    """Number above every element of this type already in the project.

    Counting the elements would reuse a number after a deletion (VS1, VS2 ->
    delete VS1 -> the next valve is VS2 again), so the highest short name / name
    number wins instead.
    """
    numbers = [
        _trailing_number((doc.get("properties") or {}).get("short_name"))
        or _trailing_number(doc.get("name_en"))
        or 0
        for doc in mongo.db.network_elements.find(
            {"project_id": project_id, "type": element_type},
            {"properties.short_name": 1, "name_en": 1},
        )
    ]
    return (max(numbers) if numbers else 0) + 1


#: Labels for the connector kinds, so the table and the dropdown agree.
CONNECTOR_TYPES = {
    "coupling": ("Coupling", "كبسولة"),
    "tee": ("Tee", "تي"),
    "elbow": ("Elbow", "كوع"),
    "reducer": ("Reducer", "مختزل"),
    "adapter": ("Adapter", "محول"),
}


def _mm_cell(value, title=""):
    return {"en": "%g" % value if value else "—", "ar": "", "title": title}


def _cells_connector(properties):
    label_en, label_ar = CONNECTOR_TYPES.get(
        properties.get("connector_type"), ("—", "")
    )
    return [
        {"en": label_en, "ar": label_ar, "badge": True},
        _mm_cell(properties.get("diameter"), "Connector diameter"),
    ]


def _cells_reductor(properties):
    upstream = properties.get("from_diameter")
    downstream = properties.get("to_diameter")
    if upstream and downstream:
        return [{"en": "%g → %g" % (upstream, downstream), "ar": "", "badge": True,
                 "title": "%g mm → %g mm" % (upstream, downstream)}]
    return [{"en": "—", "ar": ""}]


def _cells_air_removal(properties):
    return [_mm_cell(properties.get("diameter"), "Air release diameter")]


def _cells_pressure_reduction(properties):
    pressure = properties.get("outlet_pressure")
    return [
        _mm_cell(properties.get("diameter"), "Reducer diameter"),
        {"en": "%g bar" % pressure if pressure else "—", "ar": "",
         "title": "Outlet pressure"},
    ]


def _cells_terminator(properties):
    return [_mm_cell(properties.get("diameter"), "Terminator diameter")]


#: Component tabs other than valves. Each one gets the same table, toolbar,
#: modals, search and map behaviour as the Valves tab, driven by this spec.
COMPONENT_TABS = {
    "connectors": {
        "slug": "connectors",
        "element_type": "connector",
        "prefix": "CN",
        "letter": "CN",
        "title_en": "Connectors",
        "title_ar": "الوصلات",
        "item_en": "Connector",
        "item_ar": "وصلة",
        "icon": "bi-link-45deg",
        "help_en": "Couplings, tees, elbows, reducers and adapters that join pipe ends. "
                   "Each connector is placed where it is fitted on the network.",
        "help_ar": "الكبسولات والتي والأكواع والمختزلات والمحولات التي تصل أطراف الأنابيب.",
        "fields": [
            {"name": "connector_type", "label_en": "Connector Type", "label_ar": "نوع الوصلة",
             "kind": "select", "required": True,
             "options": [{"value": key, "en": labels[0], "ar": labels[1]}
                         for key, labels in CONNECTOR_TYPES.items()]},
            {"name": "diameter", "label_en": "Diameter (mm)", "label_ar": "القطر (مم)",
             "kind": "number", "min": 16, "step": 1, "placeholder": "63"},
        ],
        "columns": [
            {"en": "Type", "ar": "النوع"},
            {"en": "Diameter (mm)", "ar": "القطر (مم)"},
        ],
    },
    "reductors": {
        "slug": "reductors",
        "element_type": "reduction",
        "prefix": "RD",
        "letter": "RD",
        "title_en": "Reductors",
        "title_ar": "المخفضات",
        "item_en": "Reductor",
        "item_ar": "مخفض",
        "icon": "bi-arrow-left-right",
        "help_en": "Diameter reductions between two pipe sizes. The upstream pipe "
                   "feeds the larger side, the downstream pipe leaves the smaller one.",
        "help_ar": "مخفضات الأقطار بين أحجام الأنابيب: الأنبوب المغذي على الجهة الكبرى "
                   "والأنبوب التالي على الجهة الصغرى.",
        "fields": [
            {"name": "from_diameter", "label_en": "From Diameter (mm)", "label_ar": "من قطر (مم)",
             "kind": "number", "min": 16, "step": 1, "required": True, "placeholder": "90"},
            {"name": "to_diameter", "label_en": "To Diameter (mm)", "label_ar": "إلى قطر (مم)",
             "kind": "number", "min": 16, "step": 1, "required": True, "placeholder": "63"},
        ],
        "columns": [
            {"en": "Reduction (mm)", "ar": "التخفيض (مم)"},
        ],
    },
    "air_removal": {
        "slug": "air_removal",
        "element_type": "air_release",
        "prefix": "AR",
        "letter": "AR",
        "title_en": "Air Removal",
        "title_ar": "إزالة الهواء",
        "item_en": "Air Release Valve",
        "item_ar": "صمام تنفيس",
        "icon": "bi-wind",
        "help_en": "Air release valves at the high points of the network, where air "
                   "pockets would otherwise block the flow.",
        "help_ar": "صمامات تنفيس الهواء عند أعلى نقاط الشبكة حيث تتجمع جيوب الهواء.",
        "fields": [
            {"name": "diameter", "label_en": "Diameter (mm)", "label_ar": "القطر (مم)",
             "kind": "number", "min": 16, "step": 1, "placeholder": "25"},
        ],
        "columns": [
            {"en": "Diameter (mm)", "ar": "القطر (مم)"},
        ],
    },
    "pressure_reduction": {
        "slug": "pressure_reduction",
        "element_type": "pressure_reduction",
        "prefix": "PR",
        "letter": "PR",
        "title_en": "Reduce Pressure",
        "title_ar": "خفض الضغط",
        "item_en": "Pressure Reducer",
        "item_ar": "مخفض ضغط",
        "icon": "bi-arrow-down-circle",
        "help_en": "Pressure reducing valves that keep the downstream pressure at the "
                   "set value, protecting the drippers on long runs.",
        "help_ar": "صمامات خفض الضغط التي تحافظ على الضغط في الجزء التالي من الشبكة.",
        "fields": [
            {"name": "diameter", "label_en": "Diameter (mm)", "label_ar": "القطر (مم)",
             "kind": "number", "min": 16, "step": 1, "placeholder": "50"},
            {"name": "outlet_pressure", "label_en": "Outlet Pressure (bar)",
             "label_ar": "ضغط الخروج (بار)",
             "kind": "number", "min": 0, "step": 0.1, "placeholder": "1.5"},
        ],
        "columns": [
            {"en": "Diameter (mm)", "ar": "القطر (مم)"},
            {"en": "Outlet (bar)", "ar": "الخروج (بار)"},
        ],
    },
    "terminators": {
        "slug": "terminators",
        "element_type": "terminator",
        "prefix": "TM",
        "letter": "TM",
        "title_en": "Terminators",
        "title_ar": "النهايات",
        "item_en": "Terminator",
        "item_ar": "نهاية",
        "icon": "bi-stop-circle",
        "help_en": "End caps and flush valves that close the end of a line.",
        "help_ar": "سدادات الصفوف وصمامات الغسيل التي تُغلق نهاية الخط.",
        "fields": [
            {"name": "diameter", "label_en": "Diameter (mm)", "label_ar": "القطر (مم)",
             "kind": "number", "min": 16, "step": 1, "placeholder": "16"},
        ],
        "columns": [
            {"en": "Diameter (mm)", "ar": "القطر (مم)"},
        ],
    },
}

#: Builds the extra table cells of a row from the element's properties.
CELL_BUILDERS = {
    "connectors": _cells_connector,
    "reductors": _cells_reductor,
    "air_removal": _cells_air_removal,
    "pressure_reduction": _cells_pressure_reduction,
    "terminators": _cells_terminator,
}


def get_project_or_redirect(project_id):
    """Project the signed-in user may open, or ``None`` after a flash."""
    from irrigation.access import require_project

    project = require_project(project_id)
    if not project:
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


def _remove_elements(project_id, allowed_types, endpoint="components.valves"):
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
        return redirect(url_for(endpoint, project_id=project_id))

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
    return redirect(url_for(endpoint, project_id=project_id))


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
        item.pop("_id", None)

    sector_names = {sector["id"]: sector for sector in sectors}
    zone_names = {zone["id"]: zone for zone in zones}

    valves = []
    type_counters: dict = {}
    for valve in mongo.db.network_elements.find(
        {"project_id": project_id, "type": {"$in": VALVE_TYPES}}
    ).sort("id", 1):
        valve.pop("_id", None)
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

    number = _next_element_number(project_id, valve_type)
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
    return _remove_elements(project_id, VALVE_TYPES, endpoint="components.valves")


# === COMPONENT TABS (connectors, reductors, ...) =============================
#
# Every tab below behaves exactly like Valves: the same toolbar (add, edit,
# rename, delete, validate), the same searchable table, and a map whose markers
# select the matching row.  The differences live in COMPONENT_TABS, so the route
# and the template are shared instead of copied five times.


def _tab_or_404(key):
    from flask import abort

    tab = COMPONENT_TABS.get(key)
    if not tab:
        abort(404)
    return tab


def _element_number(element, fallback):
    properties = element.get("properties") or {}
    return (
        _trailing_number(properties.get("short_name"))
        or _trailing_number(element.get("name_en"))
        or fallback
    )


def _collect_elements(project_id, tab):
    """Rows of one component tab: names, short name, table cells, coordinates."""
    cell_builder = CELL_BUILDERS[tab["slug"]]
    elements = []
    query = mongo.db.network_elements.find(
        {"project_id": project_id, "type": tab["element_type"]}
    ).sort("id", 1)
    for ordinal, element in enumerate(query, start=1):
        # Documents imported outside insert_one() may not carry an ObjectId yet.
        element.pop("_id", None)
        properties = element.get("properties") or {}
        element["properties"] = properties
        element["short_name"] = properties.get("short_name") or "%s%d" % (
            tab["prefix"],
            _element_number(element, ordinal),
        )
        element["coordinates"] = (element.get("geometry") or {}).get("coordinates")
        element["cells"] = cell_builder(properties)
        elements.append(element)
    return elements


#: Geometry that no Components map draws: only zones and pipes stay as context.
BASE_HIDDEN_TYPES = ("sector", "tree_row", "tree")


def _hidden_types(key):
    """Feature types a component tab's map must not draw.

    Tabs are ordered in COMPONENT_TABS, valves coming first: a tab hides the
    valves and every kind handled by the tabs above it (Connectors hides valves,
    Reductors hides valves and connectors, ...).  Sectors, rows and trees are
    never drawn on a Components map.
    """
    hidden = list(BASE_HIDDEN_TYPES) + list(VALVE_TYPES)
    for slug, tab in COMPONENT_TABS.items():
        if slug == key:
            break
        hidden.append(tab["element_type"])
    return hidden


def _render_tab(project_id, key):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))
    tab = _tab_or_404(key)
    return render_template(
        "components/component_tab.html",
        project=project,
        tab=tab,
        elements=_collect_elements(project_id, tab),
        hidden_types=_hidden_types(key),
    )


def _point_from_form():
    """``(geometry, error)`` from the coordinates field of the submitted form."""
    raw = (request.form.get("coordinates") or "").strip()
    if not raw:
        return None, None
    try:
        coords = json.loads(raw)
    except (TypeError, ValueError):
        return None, "Invalid coordinates / إحداثيات غير صالحة"
    if not (isinstance(coords, list) and len(coords) == 2
            and all(isinstance(value, (int, float)) for value in coords)):
        return None, "Invalid coordinates / إحداثيات غير صالحة"
    return {"type": "Point", "coordinates": coords}, None


def _properties_from_form(tab, base=None):
    """Read the tab's spec fields out of the submitted form.

    A blank field removes the stored value, so clearing the diameter in the edit
    form really clears it instead of keeping the old number.
    """
    properties = dict(base or {})
    for field in tab["fields"]:
        raw = (request.form.get(field["name"]) or "").strip()
        if not raw:
            properties.pop(field["name"], None)
            continue
        if field["kind"] == "number":
            try:
                number = float(raw)
            except ValueError:
                continue
            properties[field["name"]] = int(number) if number.is_integer() else number
        else:
            properties[field["name"]] = raw
    return properties


@components_bp.route("/<int:project_id>/connectors")
@login_required
def connectors(project_id):
    return _render_tab(project_id, "connectors")


@components_bp.route("/<int:project_id>/reductors")
@login_required
def reductors(project_id):
    return _render_tab(project_id, "reductors")


@components_bp.route("/<int:project_id>/air_removal")
@login_required
def air_removal(project_id):
    return _render_tab(project_id, "air_removal")


@components_bp.route("/<int:project_id>/pressure_reduction")
@login_required
def pressure_reduction(project_id):
    return _render_tab(project_id, "pressure_reduction")


@components_bp.route("/<int:project_id>/terminators")
@login_required
def terminators(project_id):
    return _render_tab(project_id, "terminators")


@components_bp.route("/<int:project_id>/<slug>/add", methods=["POST"])
@login_required
def component_add(project_id, slug):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))
    tab = _tab_or_404(slug)
    page = url_for("components.%s" % slug, project_id=project_id)

    geometry, error = _point_from_form()
    if error:
        flash(error, "danger")
        return redirect(page)
    if not geometry:
        flash(
            "Pick the location on the map first / اختر الموقع على الخريطة أولا",
            "danger",
        )
        return redirect(page)

    number = _next_element_number(project_id, tab["element_type"])
    properties = _properties_from_form(tab)
    properties["source"] = "manual"
    properties["short_name"] = "%s%d" % (tab["prefix"], number)
    add_network_element(
        project_id,
        tab["element_type"],
        "%s %d" % (tab["item_en"], number),
        "%s %d" % (tab["item_ar"], number),
        geometry,
        properties,
    )
    _invalidate_piping_report(project_id)
    flash(
        "%s added / تمت إضافة %s" % (tab["item_en"], tab["item_ar"]), "success"
    )
    return redirect(page)


@components_bp.route("/<int:project_id>/<slug>/<int:element_id>/edit", methods=["POST"])
@login_required
def component_edit(project_id, slug, element_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))
    tab = _tab_or_404(slug)
    page = url_for("components.%s" % slug, project_id=project_id)

    element = mongo.db.network_elements.find_one(
        {"project_id": project_id, "id": element_id, "type": tab["element_type"]}
    )
    if not element:
        flash("Element not found / العنصر غير موجود", "danger")
        return redirect(page)

    updates = {"properties": _properties_from_form(tab, element.get("properties"))}

    name_en = (request.form.get("name_en") or "").strip()
    name_ar = (request.form.get("name_ar") or "").strip()
    if name_en or name_ar:
        updates["name_en"] = name_en or name_ar
        updates["name_ar"] = name_ar

    geometry, error = _point_from_form()
    if error:
        flash(error, "danger")
        return redirect(page)
    if geometry:
        updates["geometry"] = geometry

    mongo.db.network_elements.update_one(
        {"project_id": project_id, "id": element_id}, {"$set": updates}
    )
    _invalidate_piping_report(project_id)
    flash("Element updated / تم تحديث العنصر", "success")
    return redirect(page)


@components_bp.route("/<int:project_id>/<slug>/<int:element_id>/rename", methods=["POST"])
@login_required
def component_rename(project_id, slug, element_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))
    tab = _tab_or_404(slug)
    page = url_for("components.%s" % slug, project_id=project_id)

    name_en = (request.form.get("name_en") or "").strip()
    name_ar = (request.form.get("name_ar") or "").strip()
    if not name_en and not name_ar:
        flash("A name is required / الاسم مطلوب", "danger")
        return redirect(page)

    result = mongo.db.network_elements.update_one(
        {"project_id": project_id, "id": element_id, "type": tab["element_type"]},
        {"$set": {"name_en": name_en or name_ar, "name_ar": name_ar}},
    )
    if result.matched_count:
        flash("Renamed / تمت إعادة التسمية", "success")
    else:
        flash("Element not found / العنصر غير موجود", "danger")
    return redirect(page)


@components_bp.route("/<int:project_id>/<slug>/validate", methods=["POST"])
@login_required
def component_validate(project_id, slug):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for("project.index"))
    tab = _tab_or_404(slug)
    mongo.db.network_elements.update_many(
        {"project_id": project_id, "type": tab["element_type"]},
        {"$set": {"validated": True}},
    )
    flash(
        "%s validated / تم التحقق من %s" % (tab["title_en"], tab["title_ar"]),
        "success",
    )
    return redirect(url_for("components.%s" % slug, project_id=project_id))


@components_bp.route("/<int:project_id>/<slug>/remove", methods=["POST"])
@login_required
def component_remove(project_id, slug):
    tab = _tab_or_404(slug)
    return _remove_elements(
        project_id, [tab["element_type"]], endpoint="components.%s" % slug
    )
