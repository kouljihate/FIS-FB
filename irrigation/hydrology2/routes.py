"""Hydrology 2 blueprint: driplines, reductions, air eliminators and connectors.

These are the components downstream of the zone valve.  The dripline page also
reports the emitter discharge implied by the row spacing and the system pressure,
which is the number that decides whether the block will irrigate evenly.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..constants import FITTING_KINDS
from ..utils import crud
from ..utils.actions import add_record, remove_record, rename_record, update_record
from ..utils.decorators import project_access_required
from ..utils.emitters import emitter_flow_lph

hydrology2_bp = Blueprint("hydrology2", __name__, url_prefix="/hydrology2")

KIND_KEYS = {key for key, _ in FITTING_KINDS}
KIND_LABELS = dict(FITTING_KINDS)

#: Endpoint suffix per kind, kept explicit so the URLs stay readable.
PAGES = {
    "dripline": "dripline",
    "dripper": "dripline",
    "reduction": "reduction",
    "pressure_reduction": "pressure_reduction",
    "air_eliminator": "air_eliminator",
    "connector": "connectors",
}

FIELDS = ("diameter_mm", "spacing_m", "flow_lph", "reduction_ratio",
          "setting_m", "zone_id", "notes")

COLUMNS = (
    ("Diameter (mm)", "diameter_mm"),
    ("Spacing (m)", "spacing_m"),
    ("Nominal flow (L/h)", "flow_lph"),
    ("Emitter flow (L/h)", "emitter_flow_lph"),
    ("Setting (m)", "setting_m"),
)


def _decorate(items, pressure_m: float = 10.0) -> list[dict]:
    for item in items:
        flow = item.get("flow_lph")
        if not flow:
            item["emitter_flow_lph"] = None
            continue
        # A dripper's rated flow is quoted at a reference pressure; scale it to
        # the working pressure through the discharge exponent.
        item["emitter_flow_lph"] = round(
            emitter_flow_lph(float(flow), p_ref_m=10.0, p_working_m=pressure_m), 3
        )
    return items


def _endpoint_for(kind: str) -> str:
    page = PAGES.get(kind, "dripline")
    return f"hydrology2.{page}"


def _redirect(project_id: str, kind: str | None = None):
    if kind:
        return redirect(url_for(_endpoint_for(kind), project_id=project_id))
    return redirect(url_for("hydrology2.dripline", project_id=project_id))


def _page(project_id: str, kind: str):
    project = request.project  # type: ignore[attr-defined]
    return render_template(
        "shared/collection.html",
        project=project,
        page_title=f"Hydrology2 - {KIND_LABELS[kind]['en']}",
        items=_decorate(crud.list_items("fittings", project["_id"], kind=kind)),
        columns=COLUMNS,
        kinds=tuple((k, v) for k, v in FITTING_KINDS if k == kind),
        kind=kind,
        fields=FIELDS,
        zones=crud.list_items("zones", project["_id"]),
        action_base=url_for(_endpoint_for(kind), project_id=project_id),
        add_route="hydrology2.add",
        rename_route="hydrology2.rename_item",
        update_route="hydrology2.update_item",
        remove_route="hydrology2.remove_item",
    )


@hydrology2_bp.get("/<project_id>/dripline")
@project_access_required
def dripline(project_id: str):
    return _page(project_id, "dripline")


@hydrology2_bp.get("/<project_id>/reduction")
@project_access_required
def reduction(project_id: str):
    return _page(project_id, "reduction")


@hydrology2_bp.get("/<project_id>/pressure-reduction")
@project_access_required
def pressure_reduction(project_id: str):
    return _page(project_id, "pressure_reduction")


@hydrology2_bp.get("/<project_id>/air-eliminator")
@project_access_required
def air_eliminator(project_id: str):
    return _page(project_id, "air_eliminator")


@hydrology2_bp.get("/<project_id>/connectors")
@project_access_required
def connectors(project_id: str):
    return _page(project_id, "connector")


@hydrology2_bp.post("/<project_id>/add")
@project_access_required
def add(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    kind = request.form.get("kind") or "dripline"
    if kind not in KIND_KEYS:
        flash("Unknown component type.", "danger")
        return _redirect(project_id)
    add_record("fittings", project["_id"], kind=kind, unique_kind=True,
               fields=FIELDS)
    latest = crud.list_items("fittings", project["_id"],
                             sort=[("created_at", -1)])[:1]
    if latest:
        crud.update_item("fittings", project["_id"], latest[0]["_id"],
                         {"zone_id": crud.oid(request.form.get("zone_id"))})
    return _redirect(project_id, kind)


@hydrology2_bp.post("/<project_id>/<item_id>/rename")
@project_access_required
def rename_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    rename_record("fittings", project["_id"], item_id,
                  kind=request.form.get("kind"), unique_kind=True)
    return _redirect(project_id, request.form.get("kind"))


@hydrology2_bp.post("/<project_id>/<item_id>/update")
@project_access_required
def update_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    update_record("fittings", project["_id"], item_id, fields=FIELDS)
    crud.update_item("fittings", project["_id"], item_id,
                     {"zone_id": crud.oid(request.form.get("zone_id"))})
    return _redirect(project_id, request.form.get("kind"))


@hydrology2_bp.post("/<project_id>/<item_id>/remove")
@project_access_required
def remove_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    remove_record("fittings", project["_id"], item_id)
    return _redirect(project_id, request.form.get("kind"))
