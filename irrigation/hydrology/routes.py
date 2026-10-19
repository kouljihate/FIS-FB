"""Hydrology 1 blueprint: Main Pipe (MP), Secondary Pipe (SP), Zone Pipe (ZP).

Pipes live in one collection keyed by ``level``.  Each row carries the diameter,
length and material, and the page shows the derived velocity and Hazen-Williams
head loss so a diameter can be judged before the network is even connected.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..constants import PIPE_LEVELS
from ..utils import crud
from ..utils.actions import add_record, remove_record, rename_record, update_record
from ..utils.decorators import project_access_required
from ..utils.hydraulics import hazen_williams_headloss_m, velocity_m_s

hydrology_bp = Blueprint("hydrology", __name__, url_prefix="/hydrology")

LEVEL_KEYS = {key for key, _ in PIPE_LEVELS}
LEVEL_LABELS = dict(PIPE_LEVELS)

FIELDS = ("diameter_mm", "length_m", "material", "inlet_pressure_m",
          "sector_id", "zone_id", "notes")

COLUMNS = (
    ("Diameter (mm)", "diameter_mm"),
    ("Length (m)", "length_m"),
    ("Material", "material"),
    ("Velocity (m/s)", "velocity_m_s"),
    ("Head loss (m)", "head_loss_m"),
)


def _decorate(items) -> list[dict]:
    """Attach the derived velocity and friction loss to each pipe row.

    A nominal flow is assumed when the row does not carry one, so the table is
    useful immediately after a pipe is added; the real design flow arrives with
    the network sizing phase.
    """
    settings_flow = 6.0 / 3600.0  # 6 m3/h placeholder until the network is sized
    for item in items:
        diameter_m = (item.get("diameter_mm") or 0) / 1000.0
        length_m = item.get("length_m") or 0.0
        flow = item.get("design_flow_m3s") or settings_flow
        if diameter_m > 0:
            item["velocity_m_s"] = round(velocity_m_s(flow, diameter_m), 3)
            item["head_loss_m"] = round(
                hazen_williams_headloss_m(flow, length_m, diameter_m), 3
            )
        else:
            item["velocity_m_s"] = None
            item["head_loss_m"] = None
    return items


def _redirect(project_id: str, level: str | None = None):
    endpoint = {
        "MP": "hydrology.main_pipe",
        "SP": "hydrology.secondary_pipe",
        "ZP": "hydrology.zone_pipe",
    }.get(level or "MP", "hydrology.main_pipe")
    return redirect(url_for(endpoint, project_id=project_id))


def _page(project_id: str, level: str):
    project = request.project  # type: ignore[attr-defined]
    # The pipe level is stored as the record's ``kind`` discriminator.
    items = _decorate(crud.list_items("pipes", project["_id"], kind=level))
    return render_template(
        "shared/collection.html",
        project=project,
        page_title=f"Hydrology1 - {LEVEL_LABELS[level]['en']}",
        items=items,
        columns=COLUMNS,
        kinds=tuple((k, v) for k, v in PIPE_LEVELS if k == level),
        kind=level,
        level=level,
        fields=FIELDS,
        sectors=crud.list_items("sectors", project["_id"]),
        zones=crud.list_items("zones", project["_id"]),
        action_base=url_for("hydrology.main_pipe" if level == "MP"
                            else ("hydrology.secondary_pipe" if level == "SP"
                                  else "hydrology.zone_pipe"), project_id=project_id),
        add_route="hydrology.add",
        rename_route="hydrology.rename_item",
        update_route="hydrology.update_item",
        remove_route="hydrology.remove_item",
        kind_field="level",
    )


@hydrology_bp.get("/<project_id>/main-pipe")
@project_access_required
def main_pipe(project_id: str):
    return _page(project_id, "MP")


@hydrology_bp.get("/<project_id>/secondary-pipe")
@project_access_required
def secondary_pipe(project_id: str):
    return _page(project_id, "SP")


@hydrology_bp.get("/<project_id>/zone-pipe")
@project_access_required
def zone_pipe(project_id: str):
    return _page(project_id, "ZP")


@hydrology_bp.post("/<project_id>/add")
@project_access_required
def add(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    level = (request.form.get("level") or "MP").upper()
    if level not in LEVEL_KEYS:
        flash("Unknown pipe level.", "danger")
        return _redirect(project_id)
    # The level is stored twice on purpose: as ``kind`` it discriminates the
    # record for uniqueness, and as ``level`` the document describes itself, so
    # reports, the API and templates do not have to know about the convention.
    fields = {"level": level,
              "sector_id": crud.oid(request.form.get("sector_id")),
              "zone_id": crud.oid(request.form.get("zone_id"))}
    add_record("pipes", project["_id"], kind=level, unique_kind=True,
               fields=FIELDS, extra={"level": level})
    latest = crud.list_items("pipes", project["_id"],
                             sort=[("created_at", -1)])[:1]
    if latest:
        crud.update_item("pipes", project["_id"], latest[0]["_id"], fields)
    return _redirect(project_id, level)


@hydrology_bp.post("/<project_id>/<item_id>/rename")
@project_access_required
def rename_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    rename_record("pipes", project["_id"], item_id,
                  kind=(request.form.get("level") or "MP").upper(), unique_kind=True)
    return _redirect(project_id, request.form.get("level"))


@hydrology_bp.post("/<project_id>/<item_id>/update")
@project_access_required
def update_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    fields = {"sector_id": crud.oid(request.form.get("sector_id")),
              "zone_id": crud.oid(request.form.get("zone_id"))}
    update_record("pipes", project["_id"], item_id, fields=FIELDS)
    level = (request.form.get("level") or "MP").upper()
    crud.update_item("pipes", project["_id"], item_id,
                     {**fields, "level": level})
    return _redirect(project_id, request.form.get("level"))


@hydrology_bp.post("/<project_id>/<item_id>/remove")
@project_access_required
def remove_item(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    remove_record("pipes", project["_id"], item_id)
    return _redirect(project_id, request.form.get("level"))
