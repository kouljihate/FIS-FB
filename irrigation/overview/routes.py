"""Map Overview tab.

One big map for the whole project.  A row of buttons zooms the map to one
sector at a time, and a second row works on the features picked on the map:
edit, rename or delete the selected object, whatever its kind (sector, zone,
row, tree, pipe, valve or fitting).

The tab is read/write but deliberately thin: it edits names plus the few
attributes that make sense on a map (diameters, connector type, outlet
pressure, tree variety).  Geometry itself is still drawn on the section pages.
"""

from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from irrigation.access import require_project
from irrigation.extensions import mongo
from irrigation.models import assign_short_names

overview_bp = Blueprint("overview", __name__, url_prefix="/overview")

PIPE_TYPES = ("main_pipe", "second_pipe", "sub_pipe", "dripline")
VALVE_TYPES = ("master_valve", "sector_valve", "zone_valve", "check_valve")
FITTING_TYPES = ("connector", "reduction", "air_release", "pressure_reduction", "terminator")

CONNECTOR_OPTIONS = [
    {"value": "coupling", "en": "Coupling", "ar": "كبسولة"},
    {"value": "tee", "en": "Tee", "ar": "تي"},
    {"value": "elbow", "en": "Elbow", "ar": "كوع"},
    {"value": "reducer", "en": "Reducer", "ar": "مختزل"},
    {"value": "adapter", "en": "Adapter", "ar": "محول"},
]


def _diameter_field(name="diameter", label_en="Diameter (mm)", label_ar="القطر (مم)"):
    return {"name": name, "label_en": label_en, "label_ar": label_ar,
            "kind": "number", "min": 0, "step": 1}


#: Every feature kind the Map Overview can edit.  ``storage`` says where the
#: extra fields live: on the document (geometry tables) or in ``properties``
#: (network elements: pipes, valves and fittings).
EDITABLE = {
    "sector": {"collection": "sectors", "label_en": "Sector", "label_ar": "قطاع",
               "storage": "document", "fields": []},
    "zone": {"collection": "zones", "label_en": "Zone", "label_ar": "منطقة",
             "storage": "document", "fields": []},
    "tree_row": {"collection": "tree_rows", "label_en": "Row", "label_ar": "صف",
                 "storage": "document", "fields": []},
    "tree": {"collection": "trees", "label_en": "Tree", "label_ar": "شجرة",
             "storage": "document", "fields": [
                 {"name": "variety_en", "label_en": "Variety (EN)", "label_ar": "الصنف",
                  "kind": "text"},
                 {"name": "variety_ar", "label_en": "Variety (AR)", "label_ar": "الصنف بالعربية",
                  "kind": "text"},
                 {"name": "drips_per_tree", "label_en": "Drips per tree", "label_ar": "النقاطات لكل شجرة",
                  "kind": "number", "min": 0, "step": 1},
             ]},
    "main_pipe": {"collection": "network_elements", "label_en": "Main Pipe",
                  "label_ar": "الأنبوب الرئيسي", "storage": "properties",
                  "fields": [_diameter_field()]},
    "second_pipe": {"collection": "network_elements", "label_en": "Second Pipe",
                    "label_ar": "الأنبوب الثانوي", "storage": "properties",
                    "fields": [_diameter_field()]},
    "sub_pipe": {"collection": "network_elements", "label_en": "Zone Pipe",
                 "label_ar": "أنبوب المنطقة", "storage": "properties",
                 "fields": [_diameter_field()]},
    "dripline": {"collection": "network_elements", "label_en": "Dripline",
                 "label_ar": "خط التنقيط", "storage": "properties",
                 "fields": [_diameter_field()]},
    "master_valve": {"collection": "network_elements", "label_en": "Master Valve",
                     "label_ar": "الصمام الرئيسي", "storage": "properties",
                     "fields": [_diameter_field()]},
    "sector_valve": {"collection": "network_elements", "label_en": "Sector Valve",
                     "label_ar": "صمام القطاع", "storage": "properties",
                     "fields": [_diameter_field()]},
    "zone_valve": {"collection": "network_elements", "label_en": "Zone Valve",
                   "label_ar": "صمام المنطقة", "storage": "properties",
                   "fields": [_diameter_field()]},
    "check_valve": {"collection": "network_elements", "label_en": "Check Valve",
                    "label_ar": "صمام عدم الرجوع", "storage": "properties",
                    "fields": [_diameter_field()]},
    "connector": {"collection": "network_elements", "label_en": "Connector",
                  "label_ar": "وصلة", "storage": "properties",
                  "fields": [
                      {"name": "connector_type", "label_en": "Connector Type",
                       "label_ar": "نوع الوصلة", "kind": "select",
                       "options": CONNECTOR_OPTIONS},
                      _diameter_field(),
                  ]},
    "reduction": {"collection": "network_elements", "label_en": "Reductor",
                  "label_ar": "مخفض", "storage": "properties",
                  "fields": [
                      _diameter_field("from_diameter", "From Diameter (mm)", "من قطر (مم)"),
                      _diameter_field("to_diameter", "To Diameter (mm)", "إلى قطر (مم)"),
                  ]},
    "air_release": {"collection": "network_elements", "label_en": "Air Release",
                    "label_ar": "صمام تنفيس", "storage": "properties",
                    "fields": [_diameter_field()]},
    "pressure_reduction": {"collection": "network_elements",
                           "label_en": "Pressure Reducer", "label_ar": "مخفض ضغط",
                           "storage": "properties",
                           "fields": [
                               _diameter_field(),
                               {"name": "outlet_pressure", "label_en": "Outlet Pressure (bar)",
                                "label_ar": "ضغط الخروج (بار)", "kind": "number",
                                "min": 0, "step": 0.1},
                           ]},
    "terminator": {"collection": "network_elements", "label_en": "Terminator",
                   "label_ar": "نهاية", "storage": "properties",
                   "fields": [_diameter_field()]},
}

#: The types the map lets the user pick, in geometry order.
SELECTABLE_TYPES = (
    "sector", "zone", "tree_row", "tree",
    "main_pipe", "second_pipe", "sub_pipe", "dripline",
) + VALVE_TYPES + FITTING_TYPES


def _get_project(project_id):
    project = require_project(project_id)
    if not project:
        return None
    project["_id"] = str(project["_id"])
    return project


def _collection(spec):
    return mongo.db[spec["collection"]]


def _find_object(project_id, element_type, element_id):
    """``(spec, document)`` for one editable feature, or ``(None, None)``."""
    spec = EDITABLE.get(element_type)
    if not spec:
        return None, None
    query = {"project_id": project_id, "id": element_id}
    if spec["collection"] == "network_elements":
        query["type"] = element_type
    doc = _collection(spec).find_one(query)
    return (spec, doc) if doc else (spec, None)


def _values(doc, spec):
    props = doc.get("properties") or {}
    values = {}
    for field in spec["fields"]:
        source = props if spec["storage"] == "properties" else doc
        value = source.get(field["name"])
        if value is not None:
            values[field["name"]] = value
    return values


def _coerce(field, raw):
    raw = (raw or "").strip()
    if raw == "":
        return None
    if field["kind"] == "number":
        try:
            number = float(raw)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return raw


def _invalidate_piping_report(project_id):
    mongo.db.piping_reports.delete_many({"project_id": project_id})


def _delete_object(project_id, spec, doc):
    """Delete one object and the children that only exist through it."""
    element_type = doc.get("type") if spec["collection"] == "network_elements" else None
    object_id = doc["id"]

    if spec["collection"] == "sectors":
        zone_ids = [z["id"] for z in mongo.db.zones.find(
            {"project_id": project_id, "sector_id": object_id}, {"id": 1})]
        if zone_ids:
            mongo.db.tree_rows.delete_many({"project_id": project_id, "zone_id": {"$in": zone_ids}})
            mongo.db.trees.delete_many({"project_id": project_id, "zone_id": {"$in": zone_ids}})
            mongo.db.network_elements.delete_many(
                {"project_id": project_id, "zone_id": {"$in": zone_ids}})
            mongo.db.zones.delete_many({"project_id": project_id, "id": {"$in": zone_ids}})
        return mongo.db.sectors.delete_one(
            {"project_id": project_id, "id": object_id}).deleted_count

    if spec["collection"] == "zones":
        mongo.db.tree_rows.delete_many({"project_id": project_id, "zone_id": object_id})
        mongo.db.trees.delete_many({"project_id": project_id, "zone_id": object_id})
        mongo.db.network_elements.delete_many({"project_id": project_id, "zone_id": object_id})
        return mongo.db.zones.delete_one(
            {"project_id": project_id, "id": object_id}).deleted_count

    if spec["collection"] == "tree_rows":
        mongo.db.trees.delete_many({"project_id": project_id, "row_id": object_id})
        return mongo.db.tree_rows.delete_one(
            {"project_id": project_id, "id": object_id}).deleted_count

    if spec["collection"] == "trees":
        return mongo.db.trees.delete_one(
            {"project_id": project_id, "id": object_id}).deleted_count

    removed = mongo.db.network_elements.delete_one(
        {"project_id": project_id, "id": object_id, "type": element_type}).deleted_count
    if removed:
        _invalidate_piping_report(project_id)
    return removed


# === PAGES ===================================================================


@overview_bp.route("/<int:project_id>")
@login_required
def index(project_id):
    project = _get_project(project_id)
    if not project:
        return redirect(url_for("project.index"))

    # Short names drive the sector buttons (S1, S2, ...).  Only the sector level
    # is needed here: walking every tree of a large orchard just to label four
    # buttons kept this page loading for seconds.
    assign_short_names(project_id, levels=('sector',))
    sectors = []
    for sector in mongo.db.sectors.find({"project_id": project_id}).sort("id", 1):
        sectors.append({
            "id": sector["id"],
            "code": sector.get("short_name") or "S%d" % sector["id"],
            "name_en": sector.get("name_en", ""),
            "name_ar": sector.get("name_ar", ""),
            "has_polygon": bool(sector.get("polygon")),
        })

    return render_template(
        "overview/index.html",
        project=project,
        sectors=sectors,
        selectable_types=SELECTABLE_TYPES,
        edit_specs=EDITABLE,
    )


# === OBJECT API ==============================================================


@overview_bp.route("/<int:project_id>/object/<element_type>/<int:element_id>")
@login_required
def object_values(project_id, element_type, element_id):
    """Current name and editable attributes of one feature, for the edit form."""
    project = _get_project(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    spec, doc = _find_object(project_id, element_type, element_id)
    if not spec:
        return jsonify({"error": "Unknown element type"}), 404
    if not doc:
        return jsonify({"error": "Object not found"}), 404

    return jsonify({
        "type": element_type,
        "id": element_id,
        "name_en": doc.get("name_en", ""),
        "name_ar": doc.get("name_ar", ""),
        "values": _values(doc, spec),
    })


@overview_bp.route("/<int:project_id>/edit", methods=["POST"])
@login_required
def edit_object(project_id):
    project = _get_project(project_id)
    if not project:
        return redirect(url_for("project.index"))

    element_type = request.form.get("element_type", "")
    element_id = request.form.get("element_id", type=int)
    spec, doc = _find_object(project_id, element_type, element_id) if element_id else (None, None)
    if not spec or not doc:
        flash("Object not found / العنصر غير موجود", "danger")
        return redirect(url_for("overview.index", project_id=project_id))

    updates = {}
    name_en = (request.form.get("name_en") or "").strip()
    name_ar = (request.form.get("name_ar") or "").strip()
    if name_en:
        updates["name_en"] = name_en
    if name_ar:
        updates["name_ar"] = name_ar

    for field in spec["fields"]:
        if field["name"] not in request.form:
            continue
        value = _coerce(field, request.form.get(field["name"]))
        if value is None:
            continue
        key = "properties." + field["name"] if spec["storage"] == "properties" else field["name"]
        updates[key] = value

    if not updates:
        flash("Nothing to update / لا يوجد ما يتم تحديثه", "warning")
        return redirect(url_for("overview.index", project_id=project_id))

    query = {"project_id": project_id, "id": element_id}
    if spec["collection"] == "network_elements":
        query["type"] = element_type
    _collection(spec).update_one(query, {"$set": updates})
    if spec["collection"] == "network_elements":
        _invalidate_piping_report(project_id)
    flash("Object updated / تم تحديث العنصر", "success")
    return redirect(url_for("overview.index", project_id=project_id))


@overview_bp.route("/<int:project_id>/rename", methods=["POST"])
@login_required
def rename_object(project_id):
    project = _get_project(project_id)
    if not project:
        return redirect(url_for("project.index"))

    element_type = request.form.get("element_type", "")
    element_id = request.form.get("element_id", type=int)
    spec, doc = _find_object(project_id, element_type, element_id) if element_id else (None, None)
    if not spec or not doc:
        flash("Object not found / العنصر غير موجود", "danger")
        return redirect(url_for("overview.index", project_id=project_id))

    updates = {}
    name_en = (request.form.get("name_en") or "").strip()
    name_ar = (request.form.get("name_ar") or "").strip()
    if name_en:
        updates["name_en"] = name_en
    if name_ar:
        updates["name_ar"] = name_ar
    if not updates:
        flash("A name is required / الاسم مطلوب", "danger")
        return redirect(url_for("overview.index", project_id=project_id))

    query = {"project_id": project_id, "id": element_id}
    if spec["collection"] == "network_elements":
        query["type"] = element_type
    _collection(spec).update_one(query, {"$set": updates})
    flash("Renamed / تمت إعادة التسمية", "success")
    return redirect(url_for("overview.index", project_id=project_id))


@overview_bp.route("/<int:project_id>/delete", methods=["POST"])
@login_required
def delete_objects(project_id):
    project = _get_project(project_id)
    if not project:
        return redirect(url_for("project.index"))

    # The map can select a mix of kinds, sent as "type:id" pairs.  A single
    # element_type + element_ids list is still accepted for plain forms.
    targets = []
    for raw in request.form.getlist("elements"):
        element_type, _, raw_id = str(raw).partition(":")
        raw_id = raw_id.strip()
        if raw_id.isdigit() and element_type in EDITABLE:
            targets.append((element_type, int(raw_id)))
    if not targets:
        element_type = request.form.get("element_type", "")
        if element_type in EDITABLE:
            targets = [
                (element_type, int(value))
                for value in request.form.getlist("element_ids")
                if str(value).strip().lstrip("-").isdigit()
            ]

    if not targets:
        flash("Select at least one object first / اختر عنصرا واحدا على الأقل", "warning")
        return redirect(url_for("overview.index", project_id=project_id))

    removed = 0
    for element_type, object_id in targets:
        spec, doc = _find_object(project_id, element_type, object_id)
        if not spec or not doc:
            continue
        removed += _delete_object(project_id, spec, doc)

    flash("Removed %d object(s) / تمت إزالة %d عنصر" % (removed, removed),
          "success" if removed else "warning")
    return redirect(url_for("overview.index", project_id=project_id))
