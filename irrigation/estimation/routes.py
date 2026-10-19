"""Estimation blueprint: configuration (prices/items) and the calculated result.

One estimation document is kept per project so the configuration and the result
always agree.  Line items are ``name, unit, quantity, unit_price`` and the
monetary chain is::

    subtotal = sum(quantity * unit_price)
    discount = subtotal * discount_percent / 100
    taxable  = subtotal - discount
    tax      = taxable * tax_percent / 100
    total    = taxable + tax

Quantities can be seeded from what has actually been drawn in the other tabs, so
the estimate tracks the design instead of drifting from it.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..extensions import mongo
from ..utils import crud
from ..utils.decorators import project_access_required

estimation_bp = Blueprint("estimation", __name__, url_prefix="/estimation")

DEFAULT_ITEMS = [
    {"name": "Main pipe", "unit": "m", "quantity": 0.0, "unit_price": 0.0},
    {"name": "Secondary pipe", "unit": "m", "quantity": 0.0, "unit_price": 0.0},
    {"name": "Zone pipe", "unit": "m", "quantity": 0.0, "unit_price": 0.0},
    {"name": "Dripline", "unit": "m", "quantity": 0.0, "unit_price": 0.0},
    {"name": "Drippers", "unit": "unit", "quantity": 0.0, "unit_price": 0.0},
    {"name": "Valves", "unit": "unit", "quantity": 0.0, "unit_price": 0.0},
    {"name": "Fittings", "unit": "lot", "quantity": 0.0, "unit_price": 0.0},
    {"name": "Pump and head works", "unit": "unit", "quantity": 0.0, "unit_price": 0.0},
]


def _collection():
    return mongo.db["estimations"]


def get_estimation(project):
    doc = _collection().find_one({"project_id": project["_id"]})
    if doc is None:
        doc = {
            "project_id": project["_id"],
            "items": [dict(item) for item in DEFAULT_ITEMS],
            "discount_percent": 0.0,
            "tax_percent": 0.0,
            "notes": "",
        }
        doc["_id"] = _collection().insert_one(doc).inserted_id
    return doc


def _save(doc):
    payload = {k: v for k, v in doc.items() if k != "_id"}
    _collection().update_one({"_id": doc["_id"]}, {"$set": payload})


def _to_float(raw, default: float = 0.0) -> float:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def totals(estimation) -> dict[str, float]:
    subtotal = sum(_to_float(item.get("quantity")) * _to_float(item.get("unit_price"))
                   for item in estimation.get("items", []))
    discount_percent = _to_float(estimation.get("discount_percent"))
    tax_percent = _to_float(estimation.get("tax_percent"))
    discount = subtotal * discount_percent / 100.0
    taxable = subtotal - discount
    tax = taxable * tax_percent / 100.0
    return {
        "subtotal": round(subtotal, 2),
        "discount_percent": round(discount_percent, 2),
        "discount": round(discount, 2),
        "taxable": round(taxable, 2),
        "tax_percent": round(tax_percent, 2),
        "tax": round(tax, 2),
        "total": round(taxable + tax, 2),
        "item_count": len(estimation.get("items", [])),
    }


@estimation_bp.get("/<project_id>/configuration")
@project_access_required
def configuration(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    return render_template("estimation/configuration.html", project=project,
                           estimation=get_estimation(project))


@estimation_bp.get("/<project_id>/result")
@project_access_required
def result(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    estimation = get_estimation(project)
    lines = []
    for item in estimation.get("items", []):
        quantity = _to_float(item.get("quantity"))
        price = _to_float(item.get("unit_price"))
        lines.append({**item, "quantity": quantity, "unit_price": price,
                      "line_total": round(quantity * price, 2)})
    return render_template("estimation/result.html", project=project,
                           estimation=estimation, lines=lines,
                           totals=totals(estimation))


@estimation_bp.post("/<project_id>/configuration")
@project_access_required
def save_configuration(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    estimation = get_estimation(project)

    names = request.form.getlist("item_name")
    units = request.form.getlist("item_unit")
    quantities = request.form.getlist("item_quantity")
    prices = request.form.getlist("item_price")

    items = []
    for index, name in enumerate(names):
        label = (name or "").strip()
        if not label:
            continue
        items.append({
            "name": label,
            "unit": (units[index] if index < len(units) else "").strip(),
            "quantity": _to_float(quantities[index] if index < len(quantities) else 0),
            "unit_price": _to_float(prices[index] if index < len(prices) else 0),
        })

    estimation["items"] = items or estimation.get("items", [])
    estimation["discount_percent"] = _to_float(request.form.get("discount_percent"))
    estimation["tax_percent"] = _to_float(request.form.get("tax_percent"))
    estimation["notes"] = (request.form.get("notes") or "").strip()
    _save(estimation)

    flash("Estimation saved.", "success")
    return redirect(url_for("estimation.configuration", project_id=project_id))


@estimation_bp.post("/<project_id>/seed")
@project_access_required
def seed_from_design(project_id: str):
    """Fill quantities from the geometry and hydrology that actually exist."""
    project = request.project  # type: ignore[attr-defined]
    estimation = get_estimation(project)
    pid = project["_id"]

    pipe_lengths = {"MP": 0.0, "SP": 0.0, "ZP": 0.0}
    for pipe in crud.list_items("pipes", pid):
        level = (pipe.get("level") or "MP").upper()
        pipe_lengths[level] = pipe_lengths.get(level, 0.0) + _to_float(pipe.get("length_m"))

    dripline_length = sum(_to_float(f.get("length_m") or f.get("spacing_m"))
                          for f in crud.list_items("fittings", pid, kind="dripline"))
    drippers = crud.count_items("trees", pid)
    zones = crud.count_items("zones", pid)
    fittings = crud.count_items("fittings", pid)

    quantities = {
        "Main pipe": pipe_lengths.get("MP", 0.0),
        "Secondary pipe": pipe_lengths.get("SP", 0.0),
        "Zone pipe": pipe_lengths.get("ZP", 0.0),
        "Dripline": dripline_length,
        "Drippers": float(drippers),
        "Valves": float(zones),
        "Fittings": float(fittings),
    }
    for item in estimation.get("items", []):
        if item["name"] in quantities:
            item["quantity"] = round(quantities[item["name"]], 2)

    _save(estimation)
    flash("Quantities seeded from the current design.", "success")
    return redirect(url_for("estimation.configuration", project_id=project_id))


@estimation_bp.post("/<project_id>/reset")
@project_access_required
def reset(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    estimation = get_estimation(project)
    estimation["items"] = [dict(item) for item in DEFAULT_ITEMS]
    estimation["discount_percent"] = 0.0
    estimation["tax_percent"] = 0.0
    estimation["notes"] = ""
    _save(estimation)
    flash("Estimation reset to the default item list.", "success")
    return redirect(url_for("estimation.configuration", project_id=project_id))
