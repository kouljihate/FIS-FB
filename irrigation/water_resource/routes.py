"""Water Resource blueprint: wells, basins and other sources.

All three share one collection discriminated by ``kind`` so that a source keeps a
single identity even when the user reclassifies it, and so the hydrology stage can
query "every water source of this project" in one read.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..constants import WATER_KINDS
from ..utils import crud
from ..utils.actions import add_record, remove_record, rename_record, update_record
from ..utils.decorators import project_access_required

water_resource_bp = Blueprint("water_resource", __name__, url_prefix="/water-resource")

KIND_KEYS = {key for key, _ in WATER_KINDS}
KIND_LABELS = dict(WATER_KINDS)

FIELDS = ("flow_m3h", "static_level_m", "depth_m", "capacity_m3", "lat", "lon", "notes")

COLUMNS = (
    ("Flow (m3/h)", "flow_m3h"),
    ("Static level (m)", "static_level_m"),
    ("Depth (m)", "depth_m"),
    ("Capacity (m3)", "capacity_m3"),
    ("Notes", "notes"),
)


def _redirect(project_id: str, kind: str | None = None):
    if kind:
        return redirect(url_for("water_resource.kind", project_id=project_id, kind=kind))
    return redirect(url_for("water_resource.index", project_id=project_id))


@water_resource_bp.get("/<project_id>")
@project_access_required
def index(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    return render_template(
        "shared/collection.html",
        project=project,
        page_title="Water Resource",
        items=crud.list_items("water_resources", project["_id"]),
        columns=COLUMNS,
        kinds=WATER_KINDS,
        kind=None,
        fields=FIELDS,
        action_base=url_for("water_resource.index", project_id=project_id),
        kind_endpoint="water_resource.kind",
        add_route="water_resource.add",
        rename_route="water_resource.rename_item",
        update_route="water_resource.update_item",
        remove_route="water_resource.remove_item",
    )


@water_resource_bp.get("/<project_id>/<kind>")
@project_access_required
def kind(project_id: str, kind: str):
    if kind not in KIND_KEYS:
        flash("Unknown water resource type.", "danger")
        return _redirect(project_id)
    project = request.project  # type: ignore[attr-defined]
    return render_template(
        "shared/collection.html",
        project=project,
        page_title=f"Water Resource - {KIND_LABELS[kind]['en']}",
        items=crud.list_items("water_resources", project["_id"], kind=kind),
        columns=COLUMNS,
        kinds=WATER_KINDS,
        kind=kind,
        fields=FIELDS,
        action_base=url_for("water_resource.kind", project_id=project_id, kind=kind),
        kind_endpoint="water_resource.kind",
        add_route="water_resource.add",
        rename_route="water_resource.rename_item",
        update_route="water_resource.update_item",
        remove_route="water_resource.remove_item",
    )


@water_resource_bp.post("/<project_id>/add")
@project_access_required
def add(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    kind = request.form.get("kind") or "well"
    if kind not in KIND_KEYS:
        flash("Unknown water resource type.", "danger")
        return _redirect(project_id)
    add_record("water_resources", project["_id"], kind=kind,
               unique_kind=True, fields=FIELDS)
    return _redirect(project_id, kind)


@water_resource_bp.post("/<project_id>/<item_id>/rename")
@project_access_required
def rename_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    rename_record("water_resources", project["_id"], item_id, unique_kind=True)
    return _redirect(project_id, request.form.get("kind"))


@water_resource_bp.post("/<project_id>/<item_id>/update")
@project_access_required
def update_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    update_record("water_resources", project["_id"], item_id, fields=FIELDS)
    return _redirect(project_id, request.form.get("kind"))


@water_resource_bp.post("/<project_id>/<item_id>/remove")
@project_access_required
def remove_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    remove_record("water_resources", project["_id"], item_id)
    return _redirect(project_id, request.form.get("kind"))
