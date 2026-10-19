"""Geometry blueprint: sector and zone management.

Sectors partition the project land; zones subdivide a sector.  Both store a
``boundary`` ring plus a pre-computed ``metrics`` block (area in m^2 and ha,
perimeter, centroid, vertex count) so that tables and reports never recompute
geodesy at render time.

Split, merge and swap are real geometric operations:

* **split** cuts one boundary with a straight line through its centroid;
* **merge** unions two boundaries into their largest resulting ring;
* **swap** exchanges the *boundaries* while each row keeps its own identity,
  name and children.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..constants import COLLECTIONS
from ..extensions import mongo
from ..utils import crud
from ..utils.actions import add_record, remove_record, rename_record, update_record
from ..utils.decorators import project_access_required
from ..utils.geometry_calc import (
    GeometryError,
    describe,
    merge_polygons,
    split_into_n,
    split_polygon,
    strip_rows,
)

geometry_bp = Blueprint("geometry", __name__, url_prefix="/geometry")

SECTOR_FIELDS = ("sector_type", "crop", "soil", "irrigation_system", "notes")
ZONE_FIELDS = ("crop", "soil", "valve", "notes")


# --- helpers -----------------------------------------------------------------


def _parse_polygon(raw: str) -> list[list[float]]:
    """Parse a submitted boundary in ``x,y x,y ...`` form."""
    text = (raw or "").replace("\n", " ").replace(";", " ")
    pairs: list[list[float]] = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) != 2:
            raise GeometryError(f"expected 'x,y' pairs, got '{token}'")
        pairs.append([float(parts[0]), float(parts[1])])
    if pairs and len(pairs) < 3:
        raise GeometryError("at least 3 vertices are required")
    return pairs


def _metrics(boundary, geographic=None) -> dict:
    return describe(boundary, geographic) if boundary else {}


def _project_geographic(project) -> bool | None:
    """The project's authoritative CRS flag, recorded when the land was added.

    Falling back to ``None`` lets the geometry engine detect the CRS from the
    boundary itself, which is correct for the project's *own* outline but never
    for a derived sub-polygon.
    """
    land = project.get("land") or {}
    value = land.get("geographic")
    return value if isinstance(value, bool) else None


def _item_geographic(project, doc) -> bool | None:
    """CRS flag for a stored sector/zone, preferring its own record."""
    if doc:
        value = doc.get("geographic")
        if isinstance(value, bool):
            return value
    return _project_geographic(project)


def _boundary_geographic(project) -> bool | None:
    """CRS to use for a boundary submitted through a form.

    A manually entered boundary is assumed to share the project's CRS; if the
    project has no land yet the boundary's own shape decides.
    """
    return _project_geographic(project)


def _land_boundary(project) -> list[list[float]]:
    land = project.get("land") or {}
    boundary = land.get("boundary") or []
    if len(boundary) < 3:
        raise GeometryError(
            "this project has no land boundary yet - add one when editing the project"
        )
    return boundary


def _next_index(collection: str, project_id, prefix: str, parent: dict | None = None) -> int:
    """Highest existing ``<prefix>N`` number so generated names never collide."""
    filters = None
    if parent is not None:
        filters = {parent[0]: parent[1]}
    top = 0
    for doc in crud.list_items(collection, project_id, filters=filters):
        name = doc.get("name", "")
        if name.startswith(prefix):
            tail = name[len(prefix):]
            if tail.isdigit():
                top = max(top, int(tail))
    return top


def _two_ids(field: str = "item_ids") -> list:
    ids = request.form.getlist(field)
    if len(ids) != 2:
        flash(f"Select exactly two rows (you selected {len(ids)}).", "danger")
        return []
    return ids


# --- pages -------------------------------------------------------------------


@geometry_bp.get("/<project_id>/sectors")
@project_access_required
def sectors(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    items = crud.list_items("sectors", project["_id"])
    for item in items:
        item["zone_count"] = crud.count_items(
            "zones", project["_id"], filters={"sector_id": item["_id"]}
        )
    return render_template(
        "geometry/sectors.html",
        project=project,
        sectors=items,
        land_metrics=(project.get("land") or {}).get("metrics") or {},
        total_area_ha=sum((i.get("metrics") or {}).get("area_ha", 0) for i in items),
    )


@geometry_bp.get("/<project_id>/zones")
@project_access_required
def zones(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    sector_id = request.args.get("sector")
    items = crud.list_items("zones", project["_id"],
                            filters={"sector_id": crud.oid(sector_id)} if sector_id else None)
    return render_template(
        "geometry/zones.html",
        project=project,
        zones=items,
        sectors=crud.list_items("sectors", project["_id"]),
        selected_sector=sector_id,
        total_area_ha=sum((i.get("metrics") or {}).get("area_ha", 0) for i in items),
    )


# --- sector actions ----------------------------------------------------------


@geometry_bp.post("/<project_id>/sectors/add")
@project_access_required
def add_sector(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    try:
        boundary = _parse_polygon(request.form.get("boundary", ""))
    except (GeometryError, ValueError) as exc:
        flash(f"Boundary rejected: {exc}", "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))

    extra = {}
    if boundary:
        geographic = _boundary_geographic(project)
        extra = {"boundary": boundary, "geographic": geographic,
                 "metrics": _metrics(boundary, geographic)}
    add_record("sectors", project["_id"], fields=SECTOR_FIELDS,
               extra=extra, success="Sector added.")
    return redirect(url_for("geometry.sectors", project_id=project_id))


@geometry_bp.post("/<project_id>/sectors/<item_id>/rename")
@project_access_required
def rename_sector(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    rename_record("sectors", project["_id"], item_id)
    return redirect(url_for("geometry.sectors", project_id=project_id))


@geometry_bp.post("/<project_id>/sectors/<item_id>/update")
@project_access_required
def update_sector(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    fields = dict((k, request.form.get(k)) for k in SECTOR_FIELDS)
    raw = (request.form.get("boundary") or "").strip()
    if raw:
        try:
            boundary = _parse_polygon(raw)
        except (GeometryError, ValueError) as exc:
            flash(f"Boundary rejected: {exc}", "danger")
            return redirect(url_for("geometry.sectors", project_id=project_id))
        geographic = _boundary_geographic(project)
        fields["boundary"] = boundary
        fields["geographic"] = geographic
        fields["metrics"] = _metrics(boundary, geographic)
    update_record("sectors", project["_id"], item_id, fields=fields)
    return redirect(url_for("geometry.sectors", project_id=project_id))


@geometry_bp.post("/<project_id>/sectors/<item_id>/remove")
@project_access_required
def remove_sector(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    remove_record("sectors", project["_id"], item_id,
                  success="Sector removed with its zones and rows.")
    return redirect(url_for("geometry.sectors", project_id=project_id))


@geometry_bp.post("/<project_id>/sectors/<item_id>/split")
@project_access_required
def split_sector(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    doc = crud.get_item("sectors", project["_id"], item_id)
    if not doc or not doc.get("boundary"):
        flash("That sector has no boundary to split.", "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))

    geographic = _item_geographic(project, doc)
    try:
        parts = split_polygon(doc["boundary"],
                              mode=request.form.get("mode", "auto"),
                              ratio=float(request.form.get("ratio") or 0.5),
                              geographic=geographic)
    except (GeometryError, ValueError) as exc:
        flash(f"Split failed: {exc}", "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))

    base = doc.get("name", "S")
    crud.update_item("sectors", project["_id"], item_id,
                     {"boundary": parts[0], "geographic": geographic,
                      "metrics": _metrics(parts[0], geographic)})
    new_id = crud.add_item("sectors", project["_id"], f"{base}-B",
                           boundary=parts[1], geographic=geographic,
                           metrics=_metrics(parts[1], geographic),
                           **{k: doc.get(k) for k in SECTOR_FIELDS if doc.get(k)})
    flash(f"Sector split into two ({new_id}).", "success")
    return redirect(url_for("geometry.sectors", project_id=project_id))


@geometry_bp.post("/<project_id>/sectors/merge")
@project_access_required
def merge_sectors(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    ids = _two_ids()
    if not ids:
        return redirect(url_for("geometry.sectors", project_id=project_id))

    first = crud.get_item("sectors", project["_id"], ids[0])
    second = crud.get_item("sectors", project["_id"], ids[1])
    if not first or not second or not first.get("boundary") or not second.get("boundary"):
        flash("Both sectors need a boundary to merge.", "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))

    keep_raw = request.form.get("keep") or ids[0]
    keep_id = keep_raw if keep_raw in ids else ids[0]
    drop_id = ids[1] if keep_id == ids[0] else ids[0]

    geographic = _item_geographic(project, first)
    try:
        merged = merge_polygons(first["boundary"], second["boundary"],
                                geographic=geographic)
    except GeometryError as exc:
        flash(f"Merge failed: {exc}", "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))

    crud.update_item("sectors", project["_id"], keep_id,
                     {"boundary": merged, "geographic": geographic,
                      "metrics": _metrics(merged, geographic)})
    # Re-parent the dropped sector's zones so the geometry work is not lost.
    mongo.db[COLLECTIONS["zones"]].update_many(
        {"project_id": crud.oid(project["_id"]), "sector_id": crud.oid(drop_id)},
        {"$set": {"sector_id": crud.oid(keep_id)}},
    )
    crud.remove_item("sectors", project["_id"], drop_id, cascade=False)
    flash("Sectors merged.", "success")
    return redirect(url_for("geometry.sectors", project_id=project_id))


@geometry_bp.post("/<project_id>/sectors/swap")
@project_access_required
def swap_sectors(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    ids = _two_ids()
    if not ids:
        return redirect(url_for("geometry.sectors", project_id=project_id))
    crud.swap_items("sectors", project["_id"], ids[0], ids[1],
                    fields=("boundary", "metrics"))
    flash("Sector boundaries swapped; names and zones stayed with their sector.",
          "success")
    return redirect(url_for("geometry.sectors", project_id=project_id))


@geometry_bp.post("/<project_id>/sectors/generate")
@project_access_required
def generate_sectors(project_id: str):
    """Partition the project land into N roughly equal sectors."""
    project = request.project  # type: ignore[attr-defined]
    try:
        land = _land_boundary(project)
    except GeometryError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))

    raw_count = (request.form.get("count") or "").strip()
    if raw_count:
        try:
            count = max(1, int(float(raw_count)))
        except ValueError:
            flash("Enter a whole number of sectors.", "danger")
            return redirect(url_for("geometry.sectors", project_id=project_id))
    else:
        settings = project.get("settings") or {}
        target = float(settings.get("sector_target_ha") or 1.0)
        land_ha = (_metrics(land).get("area_ha") or 0) or 1.0
        count = max(1, round(land_ha / target))

    geographic = _project_geographic(project)
    try:
        parts = split_into_n(land, count, mode=request.form.get("mode", "auto"),
                            geographic=geographic)
    except GeometryError as exc:
        flash(f"Generation failed: {exc}", "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))

    start = _next_index("sectors", project["_id"], "S")
    for offset, part in enumerate(parts, start=1):
        crud.add_item("sectors", project["_id"], f"S{start + offset}",
                      boundary=part, geographic=geographic,
                      metrics=_metrics(part, geographic))
    flash(f"Generated {len(parts)} sectors from the land boundary.", "success")
    return redirect(url_for("geometry.sectors", project_id=project_id))


# --- zone actions ------------------------------------------------------------


def _zone_parent() -> tuple[str, object]:
    sector_id = request.form.get("sector_id") or request.args.get("sector")
    return "sector_id", crud.oid(sector_id)


def _zones_redirect(project_id: str, sector_id=None):
    if sector_id:
        return redirect(url_for("geometry.zones", project_id=project_id,
                                sector=str(sector_id)))
    return redirect(url_for("geometry.zones", project_id=project_id))


@geometry_bp.post("/<project_id>/zones/add")
@project_access_required
def add_zone(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    _, sector_oid = _zone_parent()
    if sector_oid is None:
        flash("Choose a parent sector first.", "danger")
        return _zones_redirect(project_id)

    try:
        boundary = _parse_polygon(request.form.get("boundary", ""))
    except (GeometryError, ValueError) as exc:
        flash(f"Boundary rejected: {exc}", "danger")
        return _zones_redirect(project_id, sector_oid)

    sector = crud.get_item("sectors", project["_id"], sector_oid)
    geographic = _item_geographic(project, sector)
    extra = {"sector_id": sector_oid}
    if boundary:
        extra.update({"boundary": boundary, "geographic": geographic,
                      "metrics": _metrics(boundary, geographic)})
    add_record("zones", project["_id"], fields=ZONE_FIELDS,
               extra=extra, success="Zone added.")
    return _zones_redirect(project_id, sector_oid)


@geometry_bp.post("/<project_id>/zones/<item_id>/rename")
@project_access_required
def rename_zone(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    rename_record("zones", project["_id"], item_id)
    return _zones_redirect(project_id, request.form.get("sector_id"))


@geometry_bp.post("/<project_id>/zones/<item_id>/update")
@project_access_required
def update_zone(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    fields = dict((k, request.form.get(k)) for k in ZONE_FIELDS)
    raw = (request.form.get("boundary") or "").strip()
    if raw:
        try:
            boundary = _parse_polygon(raw)
        except (GeometryError, ValueError) as exc:
            flash(f"Boundary rejected: {exc}", "danger")
            return _zones_redirect(project_id, request.form.get("sector_id"))
        existing = crud.get_item("zones", project["_id"], item_id)
        geographic = _item_geographic(project, existing)
        fields["boundary"] = boundary
        fields["geographic"] = geographic
        fields["metrics"] = _metrics(boundary, geographic)
    update_record("zones", project["_id"], item_id, fields=fields)
    return _zones_redirect(project_id, request.form.get("sector_id"))


@geometry_bp.post("/<project_id>/zones/<item_id>/remove")
@project_access_required
def remove_zone(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    remove_record("zones", project["_id"], item_id,
                  success="Zone removed with its rows and trees.")
    return _zones_redirect(project_id, request.form.get("sector_id"))


@geometry_bp.post("/<project_id>/zones/<item_id>/split")
@project_access_required
def split_zone(project_id: str, item_id: str):
    project = request.project  # type: ignore[attr-defined]
    doc = crud.get_item("zones", project["_id"], item_id)
    if not doc or not doc.get("boundary"):
        flash("That zone has no boundary to split.", "danger")
        return _zones_redirect(project_id, request.form.get("sector_id"))

    geographic = _item_geographic(project, doc)
    try:
        parts = split_polygon(doc["boundary"],
                              mode=request.form.get("mode", "auto"),
                              ratio=float(request.form.get("ratio") or 0.5),
                              geographic=geographic)
    except (GeometryError, ValueError) as exc:
        flash(f"Split failed: {exc}", "danger")
        return _zones_redirect(project_id, request.form.get("sector_id"))

    crud.update_item("zones", project["_id"], item_id,
                     {"boundary": parts[0], "geographic": geographic,
                      "metrics": _metrics(parts[0], geographic)})
    crud.add_item("zones", project["_id"], f"{doc.get('name', 'Z')}-B",
                  boundary=parts[1], geographic=geographic,
                  metrics=_metrics(parts[1], geographic),
                  sector_id=doc.get("sector_id"))
    flash("Zone split into two.", "success")
    return _zones_redirect(project_id, request.form.get("sector_id"))


@geometry_bp.post("/<project_id>/zones/merge")
@project_access_required
def merge_zones(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    ids = _two_ids()
    if not ids:
        return _zones_redirect(project_id, request.form.get("sector_id"))

    first = crud.get_item("zones", project["_id"], ids[0])
    second = crud.get_item("zones", project["_id"], ids[1])
    if not first or not second or not first.get("boundary") or not second.get("boundary"):
        flash("Both zones need a boundary to merge.", "danger")
        return _zones_redirect(project_id, request.form.get("sector_id"))

    geographic = _item_geographic(project, first)
    try:
        merged = merge_polygons(first["boundary"], second["boundary"],
                                geographic=geographic)
    except GeometryError as exc:
        flash(f"Merge failed: {exc}", "danger")
        return _zones_redirect(project_id, request.form.get("sector_id"))

    keep_id = ids[0] if (request.form.get("keep") or ids[0]) == ids[0] else ids[1]
    drop_id = ids[1] if keep_id == ids[0] else ids[0]
    crud.update_item("zones", project["_id"], keep_id,
                     {"boundary": merged, "geographic": geographic,
                      "metrics": _metrics(merged, geographic)})
    mongo.db[COLLECTIONS["rows"]].update_many(
        {"project_id": crud.oid(project["_id"]), "zone_id": crud.oid(drop_id)},
        {"$set": {"zone_id": crud.oid(keep_id)}},
    )
    crud.remove_item("zones", project["_id"], drop_id, cascade=False)
    flash("Zones merged.", "success")
    return _zones_redirect(project_id, request.form.get("sector_id"))


@geometry_bp.post("/<project_id>/zones/swap")
@project_access_required
def swap_zones(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    ids = _two_ids()
    if not ids:
        return _zones_redirect(project_id, request.form.get("sector_id"))
    crud.swap_items("zones", project["_id"], ids[0], ids[1],
                    fields=("boundary", "metrics"))
    flash("Zone boundaries swapped.", "success")
    return _zones_redirect(project_id, request.form.get("sector_id"))


@geometry_bp.post("/<project_id>/zones/generate")
@project_access_required
def generate_zones(project_id: str):
    """Split the selected sector (or every sector) into equal zones."""
    project = request.project  # type: ignore[attr-defined]
    settings = project.get("settings") or {}
    try:
        per_sector = max(1, int(float(request.form.get("count")
                                      or settings.get("zones_per_sector") or 3)))
    except (TypeError, ValueError):
        per_sector = 3

    sector_oid = crud.oid(request.form.get("sector_id") or request.args.get("sector"))
    parents = ([crud.get_item("sectors", project["_id"], sector_oid)] if sector_oid
               else crud.list_items("sectors", project["_id"]))
    parents = [p for p in parents if p and p.get("boundary")]
    if not parents:
        flash("No sector with a boundary is available to subdivide.", "danger")
        return _zones_redirect(project_id, sector_oid)

    created = 0
    for sector in parents:
        base = sector.get("name", "S")
        start = _next_index("zones", project["_id"], f"{base}-Z",
                            parent=("sector_id", sector["_id"]))
        geographic = _item_geographic(project, sector)
        try:
            parts = split_into_n(sector["boundary"], per_sector,
                                 mode=request.form.get("mode", "auto"),
                                 geographic=geographic)
        except GeometryError:
            continue
        for offset, part in enumerate(parts, start=1):
            crud.add_item("zones", project["_id"], f"{base}-Z{start + offset}",
                          boundary=part, geographic=geographic,
                          metrics=_metrics(part, geographic),
                          sector_id=sector["_id"])
            created += 1

    flash(f"Generated {created} zones across {len(parents)} sector(s).", "success")
    return _zones_redirect(project_id, sector_oid)


@geometry_bp.post("/<project_id>/sectors/<item_id>/rows-preview")
@project_access_required
def rows_preview(project_id: str, item_id: str):
    """Show how many row strips a given spacing would carve out of a sector.

    A dry run used by the Trees tab before committing rows, so the user can see
    the effect of the spacing on their own boundary.
    """
    project = request.project  # type: ignore[attr-defined]
    doc = crud.get_item("sectors", project["_id"], item_id)
    if not doc or not doc.get("boundary"):
        flash("That sector has no boundary.", "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))
    try:
        spacing = float(request.form.get("spacing_m") or 1.5)
        strips = strip_rows(doc["boundary"], spacing,
                            geographic=_item_geographic(project, doc))
    except (GeometryError, ValueError) as exc:
        flash(f"Preview failed: {exc}", "danger")
        return redirect(url_for("geometry.sectors", project_id=project_id))
    flash(f"{len(strips)} rows would be created at {spacing} m spacing.", "success")
    return redirect(url_for("geometry.sectors", project_id=project_id))
