"""Report blueprint: charts, bilan hydrique and the technical report.

The bilan hydrique runs the FAO-56 chain from :mod:`irrigation.utils.agronomy`
over the areas actually drawn in the Geometry tab, so the reported demand follows
the design rather than a hand-entered number.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..constants import FITTING_KINDS, PIPE_LEVELS
from ..extensions import mongo
from ..project import service
from ..utils import crud
from ..utils.agronomy import (
    SoilProfile,
    leaching_requirement_mm,
    pump_duty_point,
    total_dynamic_head_m,
    water_balance,
)
from ..utils.decorators import project_access_required
from ..utils.units import m3s_to_m3h, m_head_to_bar, m_head_to_psi

report_bp = Blueprint("report", __name__, url_prefix="/report")

DEFAULT_ASSUMPTIONS = {
    "et0_mm_per_day": 6.0,
    "kc": 0.85,
    "rainfall_mm_per_day": 0.0,
    "irrigation_system": "drip",
    "hours_per_day": 16.0,
    "wetted_percent": 100.0,
    "season_days": 180,
    "field_capacity": 0.30,
    "wilting_point": 0.14,
    "root_depth_m": 0.60,
    "depletion_fraction": 0.50,
    "static_lift_m": 25.0,
    "outlet_pressure_m": 10.0,
    "pump_efficiency": 0.65,
    "ec_water_ds_m": 1.0,
}


def _reports():
    return mongo.db["reports"]


def assumptions(project) -> dict:
    doc = _reports().find_one({"project_id": project["_id"]}) or {}
    merged = dict(DEFAULT_ASSUMPTIONS)
    for key, value in (doc.get("assumptions") or {}).items():
        if key in merged:
            merged[key] = value
    return merged


def _save_assumptions(project, values: dict) -> None:
    _reports().update_one(
        {"project_id": project["_id"]},
        {"$set": {"project_id": project["_id"], "assumptions": values}},
        upsert=True,
    )


def _float(form, key: str, default: float) -> float:
    raw = (form.get(key) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _irrigated_area_m2(project) -> float:
    total = sum((s.get("metrics") or {}).get("area_m2", 0.0)
                for s in crud.list_items("sectors", project["_id"]))
    if total <= 0:
        land = (project.get("land") or {}).get("metrics") or {}
        total = land.get("area_m2", 0.0)
    return total


def _pipe_totals(project) -> dict[str, float]:
    """Recorded length per pipe level.

    Prefers the self-describing ``level`` field and falls back to the ``kind``
    discriminator, so records written by either convention are attributed to the
    right level instead of silently collapsing into MP.
    """
    totals = {level: 0.0 for level, _ in PIPE_LEVELS}
    for pipe in crud.list_items("pipes", project["_id"]):
        level = (pipe.get("level") or pipe.get("kind") or "MP").upper()
        totals[level] = totals.get(level, 0.0) + float(pipe.get("length_m") or 0)
    return totals


def _level_divisor(project, level: str, pipe_count: int) -> tuple[int, str]:
    """How many parallel routes share the block flow at ``level``.

    The divisor follows the network hierarchy rather than the number of pipes
    drawn: a main carries the whole block, each secondary serves one sector and
    each zone pipe serves one zone.  Counting pipes instead would make a single
    recorded zone pipe appear to carry the entire block, which reports a
    6 m/s velocity for a pipe that in reality carries one twelfth of the flow.
    """
    if level == "MP":
        return max(1, pipe_count), "main pipes"
    if level == "SP":
        sectors = crud.count_items("sectors", project["_id"])
        return (sectors, "sectors") if sectors else (max(1, pipe_count), "secondary pipes")
    zones = crud.count_items("zones", project["_id"])
    return (zones, "zones") if zones else (max(1, pipe_count), "zone pipes")


def _critical_path(project, flow_m3s: float) -> dict:
    """Friction along the worst route, with each level carrying only its share.

    Pipes at one level are parallel routes rather than a chain, so each carries
    ``block flow / number of pipes``.  Adding every pipe in the project as if it
    were in series at the full block flow - which is what an earlier version did -
    overstates the loss by roughly the pipe count and produced heads of thousands
    of metres.
    """
    from ..utils.hydraulics import worst_branch

    detail: dict[str, dict] = {}
    total = 0.0
    for level in ("MP", "SP", "ZP"):
        branches = [
            (float(pipe.get("length_m") or 0),
             float(pipe.get("diameter_mm") or 0) / 1000.0)
            for pipe in crud.list_items("pipes", project["_id"], kind=level)
        ]
        branches = [b for b in branches if b[0] > 0 and b[1] > 0]
        if not branches:
            continue
        divisor, divisor_basis = _level_divisor(project, level, len(branches))
        share = flow_m3s / divisor
        worst = worst_branch(share, branches)
        if worst:
            worst["divisor"] = divisor
            worst["divisor_basis"] = divisor_basis
            detail[level] = worst
            total += worst["headloss_m"]
    return {"total_m": total, "levels": detail}


# --- charts ------------------------------------------------------------------


@report_bp.get("/<project_id>/charts")
@project_access_required
def charts(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    pid = project["_id"]

    sectors = [(s.get("name"), round((s.get("metrics") or {}).get("area_ha", 0), 4))
               for s in crud.list_items("sectors", pid)]
    zones = [(z.get("name"), round((z.get("metrics") or {}).get("area_ha", 0), 4))
             for z in crud.list_items("zones", pid)]
    pipes = {}
    for pipe in crud.list_items("pipes", pid):
        level = (pipe.get("level") or "MP").upper()
        pipes[level] = pipes.get(level, 0.0) + float(pipe.get("length_m") or 0)
    fittings = {}
    for fitting in crud.list_items("fittings", pid):
        kind = fitting.get("kind") or "other"
        fittings[kind] = fittings.get(kind, 0) + 1

    return render_template(
        "report/charts.html",
        project=project,
        sector_labels=[name for name, _ in sectors],
        sector_values=[value for _, value in sectors],
        zone_labels=[name for name, _ in zones],
        zone_values=[value for _, value in zones],
        pipe_labels=[level for level, _ in PIPE_LEVELS],
        pipe_values=[round(pipes.get(level, 0.0), 2) for level, _ in PIPE_LEVELS],
        kind_labels=[label["en"] for _, label in FITTING_KINDS],
        kind_values=[fittings.get(key, 0) for key, _ in FITTING_KINDS],
        stats=service.stats(pid),
    )


# --- bilan hydrique ----------------------------------------------------------


@report_bp.route("/<project_id>/bilan-hydrique", methods=["GET", "POST"])
@project_access_required
def bilan_hydrique(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    values = assumptions(project)

    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "reset":
            values = dict(DEFAULT_ASSUMPTIONS)
            _save_assumptions(project, values)
            flash("Assumptions reset to the defaults.", "success")
            return redirect(url_for("report.bilan_hydrique", project_id=project_id))

        for key, default in DEFAULT_ASSUMPTIONS.items():
            if isinstance(default, str):
                values[key] = (request.form.get(key) or default).strip()
            else:
                values[key] = _float(request.form, key, default)
        _save_assumptions(project, values)
        flash("Assumptions saved.", "success")
        return redirect(url_for("report.bilan_hydrique", project_id=project_id))

    area_m2 = _irrigated_area_m2(project)
    soil = SoilProfile(
        field_capacity=values["field_capacity"],
        wilting_point=values["wilting_point"],
        root_depth_m=values["root_depth_m"],
        depletion_fraction=values["depletion_fraction"],
    )
    balance = water_balance(
        area_m2=area_m2,
        et0_mm_per_day=values["et0_mm_per_day"],
        kc=values["kc"],
        rainfall_mm_per_day=values["rainfall_mm_per_day"],
        system=values["irrigation_system"],
        hours_per_day=values["hours_per_day"],
        soil=soil,
        season_days=int(values["season_days"]),
    )

    leach_mm, leach_fraction = leaching_requirement_mm(
        balance.net_requirement_mm_per_day, values["ec_water_ds_m"])

    pipe_totals = _pipe_totals(project)
    path = _critical_path(project, balance.peak_flow_m3s)
    friction_m = path["total_m"]
    total_head_m = total_dynamic_head_m(
        static_lift_m=values["static_lift_m"],
        friction_loss_m=friction_m,
        outlet_pressure_m=values["outlet_pressure_m"],
    )
    pump = pump_duty_point(balance.peak_flow_m3s, total_head_m,
                           values["pump_efficiency"])

    return render_template(
        "report/bilan_hydrique.html",
        project=project,
        values=values,
        area_ha=round(area_m2 / 10_000.0, 4),
        balance=balance,
        soil=soil,
        leach_mm=leach_mm,
        leach_fraction=leach_fraction,
        pipe_totals={k: round(v, 2) for k, v in pipe_totals.items()},
        path=path,
        friction_m=round(friction_m, 3),
        total_head_m=round(total_head_m, 3),
        total_head_psi=round(m_head_to_psi(total_head_m), 2),
        total_head_bar=round(m_head_to_bar(total_head_m), 3),
        pump=pump,
        peak_flow_m3h=round(m3s_to_m3h(balance.peak_flow_m3s), 2),
    )


# --- technical report --------------------------------------------------------


@report_bp.get("/<project_id>/technical")
@project_access_required
def technical(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    pid = project["_id"]

    sectors = crud.list_items("sectors", pid)
    zones = crud.list_items("zones", pid)
    rows = crud.list_items("rows", pid)
    trees = crud.list_items("trees", pid)
    pipes = crud.list_items("pipes", pid)
    sources = crud.list_items("water_resources", pid)

    area_ha = sum((s.get("metrics") or {}).get("area_ha", 0) for s in sectors)
    validation = []
    if area_ha <= 0:
        validation.append(("No sector area defined", "warning",
                           "Draw or generate sectors before issuing a report."))
    if not sources:
        validation.append(("No water source recorded", "warning",
                           "Add a well or basin so the supply can be checked."))
    if not pipes:
        validation.append(("No pipes recorded", "warning",
                           "Hydrology1 has no MP/SP/ZP rows yet."))
    if rows and not trees:
        validation.append(("Rows without trees", "info",
                           "Generate trees along the rows to complete the layout."))
    if sectors and not zones:
        validation.append(("Sectors without zones", "info",
                           "Zone subdivision is empty; valves cannot be sized."))

    return render_template(
        "report/technical.html",
        project=project,
        sectors=sectors,
        zones=zones,
        rows=rows,
        trees=trees,
        pipes=pipes,
        sources=sources,
        area_ha=round(area_ha, 4),
        stats=service.stats(pid),
        validation=validation,
        pipe_totals=_pipe_totals(project),
        assumptions=assumptions(project),
        generated_at=None,
    )
