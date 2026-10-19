"""Trees blueprint: row and tree management.

Rows are geometric objects, not just labels: a row is stored as the strip polygon
it occupies, so generating rows from a sector boundary gives real, measurable
bands.  Trees are then placed along the centreline of each strip at the requested
plant spacing, which is exactly the layout a field crew stakes out.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..utils import crud
from ..utils.actions import add_record, remove_record, rename_record, update_record
from ..utils.decorators import project_access_required
from ..utils.geometry_calc import (
    GeometryError,
    centerline_points,
    describe,
    strip_rows,
)

trees_bp = Blueprint("trees", __name__, url_prefix="/trees")

ROW_FIELDS = ("spacing_m", "azimuth_deg", "length_m", "sector_id", "zone_id", "notes")
TREE_FIELDS = ("species", "spacing_m", "age_years", "drippers_count",
               "sector_id", "zone_id", "row_id", "notes")

ROW_COLUMNS = (
    ("Sector", "sector_name"),
    ("Spacing (m)", "spacing_m"),
    ("Azimuth (deg)", "azimuth_deg"),
    ("Length (m)", "length_m"),
    ("Trees", "tree_count"),
)

TREE_COLUMNS = (
    ("Row", "row_name"),
    ("Species", "species"),
    ("Spacing (m)", "spacing_m"),
    ("Drippers", "drippers_count"),
    ("Age (y)", "age_years"),
)


def _decorate_rows(project_id, rows):
    for row in rows:
        sector = crud.get_item("sectors", project_id, row.get("sector_id") or "")
        row["sector_name"] = sector.get("name") if sector else ""
        row["tree_count"] = crud.count_items("trees", project_id,
                                             filters={"row_id": row["_id"]})
    return rows


def _decorate_trees(project_id, trees):
    for tree in trees:
        row = crud.get_item("rows", project_id, tree.get("row_id") or "")
        tree["row_name"] = row.get("name") if row else ""
    return trees


def _project_geographic(project) -> bool | None:
    """The project's CRS flag, recorded when the land boundary was saved."""
    value = (project.get("land") or {}).get("geographic")
    return value if isinstance(value, bool) else None


def _item_geographic(project, doc) -> bool | None:
    if doc:
        value = doc.get("geographic")
        if isinstance(value, bool):
            return value
    return _project_geographic(project)


def _default_spacing(project, key: str, fallback: float) -> float:
    settings = project.get("settings") or {}
    try:
        return float(settings.get(key) or fallback)
    except (TypeError, ValueError):
        return fallback


def _rows_redirect(project_id: str):
    return redirect(url_for("trees.rows", project_id=project_id))


def _trees_redirect(project_id: str):
    return redirect(url_for("trees.trees", project_id=project_id))


# --- pages -------------------------------------------------------------------


@trees_bp.get("/<project_id>/rows")
@project_access_required
def rows(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    return render_template(
        "trees/rows.html",
        project=project,
        rows=_decorate_rows(project["_id"], crud.list_items("rows", project["_id"])),
        columns=ROW_COLUMNS,
        sectors=crud.list_items("sectors", project["_id"]),
        zones=crud.list_items("zones", project["_id"]),
        fields=ROW_FIELDS,
        default_spacing=_default_spacing(project, "lateral_spacing_m", 1.5),
    )


@trees_bp.get("/<project_id>/trees")
@project_access_required
def trees(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    return render_template(
        "trees/trees.html",
        project=project,
        trees=_decorate_trees(project["_id"],
                              crud.list_items("trees", project["_id"])),
        columns=TREE_COLUMNS,
        rows=crud.list_items("rows", project["_id"]),
        fields=TREE_FIELDS,
        default_spacing=_default_spacing(project, "tree_spacing_m", 4.0),
    )


# --- row actions -------------------------------------------------------------


@trees_bp.post("/<project_id>/rows/add")
@project_access_required
def add_row(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    raw = (request.form.get("boundary") or "").strip()
    kwargs = {}
    if raw:
        try:
            boundary = [[float(p.split(",")[0]), float(p.split(",")[1])]
                        for p in raw.replace(";", " ").split()]
        except (ValueError, IndexError):
            flash("Boundary must be 'x,y x,y ...' pairs.", "danger")
            return _rows_redirect(project_id)
        kwargs = {"boundary": boundary, "metrics": describe(boundary)}
    add_record("rows", project["_id"], fields=ROW_FIELDS,
               extra=kwargs, success="Row added.")
    return _rows_redirect(project_id)


@trees_bp.post("/<project_id>/rows/<item_id>/rename")
@project_access_required
def rename_row(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    rename_record("rows", project["_id"], item_id)
    return _rows_redirect(project_id)


@trees_bp.post("/<project_id>/rows/<item_id>/update")
@project_access_required
def update_row(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    fields = {"sector_id": crud.oid(request.form.get("sector_id")),
              "zone_id": crud.oid(request.form.get("zone_id"))}
    update_record("rows", project["_id"], item_id, fields=ROW_FIELDS)
    crud.update_item("rows", project["_id"], item_id, fields)
    return _rows_redirect(project_id)


@trees_bp.post("/<project_id>/rows/<item_id>/remove")
@project_access_required
def remove_row(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    remove_record("rows", project["_id"], item_id, success="Row and its trees removed.")
    return _rows_redirect(project_id)


@trees_bp.post("/<project_id>/rows/generate")
@project_access_required
def generate_rows(project_id: str):
    """Lay out parallel row strips across the chosen sector (or every sector)."""
    project = request.project  # type: ignore[attr-defined]
    raw_spacing = (request.form.get("spacing_m") or "").strip()
    try:
        spacing = float(raw_spacing) if raw_spacing else _default_spacing(
            project, "lateral_spacing_m", 1.5)
    except ValueError:
        flash("Spacing must be a number.", "danger")
        return _rows_redirect(project_id)
    if spacing <= 0:
        flash("Spacing must be positive.", "danger")
        return _rows_redirect(project_id)

    sector_oid = crud.oid(request.form.get("sector_id") or request.args.get("sector"))
    parents = ([crud.get_item("sectors", project["_id"], sector_oid)] if sector_oid
               else crud.list_items("sectors", project["_id"]))
    parents = [p for p in parents if p and p.get("boundary")]
    if not parents:
        flash("No sector with a boundary is available.", "danger")
        return _rows_redirect(project_id)

    created = 0
    for sector in parents:
        geographic = _item_geographic(project, sector)
        try:
            strips = strip_rows(sector["boundary"], spacing,
                                geographic=geographic)
        except GeometryError as exc:
            flash(f"{sector.get('name')}: {exc}", "warning")
            continue
        base = sector.get("name", "S")
        prefix = f"{base}-R"
        start = 0
        for doc in crud.list_items("rows", project["_id"],
                                   filters={"sector_id": sector["_id"]}):
            name = doc.get("name", "")
            tail = name[len(prefix):] if name.startswith(prefix) else ""
            if tail.isdigit():
                start = max(start, int(tail))
        for offset, strip in enumerate(strips, start=1):
            metrics = describe(strip, geographic)
            crud.add_item("rows", project["_id"], f"{base}-R{start + offset}",
                          boundary=strip, geographic=geographic, metrics=metrics,
                          length_m=round(metrics.get("perimeter_m", 0) / 2.0, 2),
                          spacing_m=spacing,
                          sector_id=sector["_id"],
                          azimuth_deg=request.form.get("azimuth_deg") or None)
            created += 1

    flash(f"Generated {created} rows across {len(parents)} sector(s).", "success")
    return _rows_redirect(project_id)


# --- tree actions ------------------------------------------------------------


@trees_bp.post("/<project_id>/trees/add")
@project_access_required
def add_tree(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    row_oid = crud.oid(request.form.get("row_id"))
    row = crud.get_item("rows", project["_id"], row_oid) if row_oid else None
    add_record("trees", project["_id"], fields=TREE_FIELDS, success="Tree added.")
    latest = crud.list_items("trees", project["_id"], sort=[("created_at", -1)])[:1]
    if latest:
        patch = {"row_id": row_oid}
        if row:
            patch["sector_id"] = row.get("sector_id")
            patch["zone_id"] = row.get("zone_id")
        crud.update_item("trees", project["_id"], latest[0]["_id"], patch)
    return _trees_redirect(project_id)


@trees_bp.post("/<project_id>/trees/<item_id>/rename")
@project_access_required
def rename_tree(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    rename_record("trees", project["_id"], item_id)
    return _trees_redirect(project_id)


@trees_bp.post("/<project_id>/trees/<item_id>/update")
@project_access_required
def update_tree(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    fields = {"row_id": crud.oid(request.form.get("row_id")),
              "sector_id": crud.oid(request.form.get("sector_id")),
              "zone_id": crud.oid(request.form.get("zone_id"))}
    update_record("trees", project["_id"], item_id, fields=TREE_FIELDS)
    crud.update_item("trees", project["_id"], item_id, fields)
    return _trees_redirect(project_id)


@trees_bp.post("/<project_id>/trees/<item_id>/remove")
@project_access_required
def remove_tree(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    remove_record("trees", project["_id"], item_id)
    return _trees_redirect(project_id)


@trees_bp.post("/<project_id>/trees/generate")
@project_access_required
def generate_trees(project_id: str):
    """Place trees along the centreline of the chosen rows."""
    project = request.project  # type: ignore[attr-defined]
    raw_spacing = (request.form.get("spacing_m") or "").strip()
    try:
        spacing = float(raw_spacing) if raw_spacing else _default_spacing(
            project, "tree_spacing_m", 4.0)
    except ValueError:
        flash("Spacing must be a number.", "danger")
        return _trees_redirect(project_id)
    if spacing <= 0:
        flash("Spacing must be positive.", "danger")
        return _trees_redirect(project_id)

    row_oid = crud.oid(request.form.get("row_id") or request.args.get("row"))
    targets = ([crud.get_item("rows", project["_id"], row_oid)] if row_oid
               else crud.list_items("rows", project["_id"]))
    targets = [r for r in targets if r and r.get("boundary")]
    if not targets:
        flash("No row with a boundary is available - generate rows first.", "danger")
        return _trees_redirect(project_id)

    species = (request.form.get("species") or "").strip()
    drippers = request.form.get("drippers_count") or None
    created = 0
    for row in targets:
        geographic = _item_geographic(project, row)
        try:
            points = centerline_points(row["boundary"], spacing,
                                       geographic=geographic)
        except GeometryError as exc:
            flash(f"{row.get('name')}: {exc}", "warning")
            continue
        base = row.get("name", "R")
        for index, point in enumerate(points, 1):
            crud.add_item("trees", project["_id"], f"{base}-T{index}",
                          lat=point[0], lon=point[1],
                          species=species or None,
                          spacing_m=spacing,
                          drippers_count=drippers,
                          row_id=row["_id"],
                          sector_id=row.get("sector_id"),
                          zone_id=row.get("zone_id"))
            created += 1

    flash(f"Generated {created} trees across {len(targets)} row(s).", "success")
    return _trees_redirect(project_id)
