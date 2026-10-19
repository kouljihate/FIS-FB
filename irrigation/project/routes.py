"""Project blueprint: landing page, list, creation, edition and dashboard."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from ..constants import COLLECTIONS
from ..utils import mapfiles
from ..utils.decorators import project_access_required
from ..utils.geometry_calc import GeometryError, describe, is_geographic
from . import service

project_bp = Blueprint("project", __name__)


@project_bp.get("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("project.list_projects"))
    return redirect(url_for("auth.login"))


@project_bp.get("/projects")
@login_required
def list_projects():
    projects = service.list_projects_for(current_user)
    cards = [
        {
            "doc": p,
            "stats": service.stats(p["_id"]),
            # The card only offers Remove where the request would be allowed.
            "can_delete": service.can_delete(current_user, p),
        }
        for p in projects
    ]
    return render_template("project/list.html", cards=cards)


@project_bp.route("/projects/new", methods=["GET", "POST"])
@login_required
def create_project():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()

        upload = request.files.get("map_file")
        if not upload or not upload.filename:
            flash(f"A map file is required: upload the land as {mapfiles.FORMATS}.",
                  "danger")
            return render_template("project/create.html", form=request.form,
                                   map_accept=mapfiles.ACCEPT)

        if not name:
            flash("A project name is required.", "danger")
            return render_template("project/create.html", form=request.form,
                                   map_accept=mapfiles.ACCEPT)

        try:
            kind, data, boundary, geographic = _boundary_from_map(upload)
            metrics = describe(boundary, geographic)
        except (mapfiles.MapFileError, GeometryError, ValueError) as exc:
            flash(f"Map rejected: {exc}", "danger")
            return render_template("project/create.html", form=request.form,
                                   map_accept=mapfiles.ACCEPT)

        # The boundary is stored together with its coordinate system. Every later
        # operation on this project's geometry reuses the flag, so a derived
        # sub-polygon can never be reinterpreted as lon/lat.
        land = {"boundary": boundary, "geographic": geographic, "metrics": metrics}
        project_id = service.create_project(current_user, name,
                                            description=description, land=land)
        _link_map(project_id, upload, kind, data)
        flash("Project created and the map linked.", "success")
        return redirect(url_for("project.overview", project_id=project_id))

    return render_template("project/create.html", map_accept=mapfiles.ACCEPT)


@project_bp.route("/projects/<project_id>/edit", methods=["GET", "POST"])
@project_access_required
def edit_project(project_id: str):
    """Change a project's identity, its land boundary or its linked map."""
    project = request.project  # type: ignore[attr-defined]

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        raw_boundary = (request.form.get("boundary") or "").strip()
        upload = request.files.get("map_file")

        if not name:
            flash("A project name is required.", "danger")
            return _render_edit(project, form=request.form)

        fields: dict = {"name": name, "description": description}
        if upload and upload.filename:
            # A new map replaces the old one and redraws the boundary.
            try:
                kind, data, boundary, geographic = _boundary_from_map(upload)
            except (mapfiles.MapFileError, GeometryError, ValueError) as exc:
                flash(f"Map rejected: {exc}", "danger")
                return _render_edit(project, form=request.form)
            fields["land"] = {"boundary": boundary, "geographic": geographic,
                              "metrics": describe(boundary, geographic)}
            fields["map"] = _link_map(project["_id"], upload, kind, data)
        elif raw_boundary:
            # Typed coordinates are the fallback when no new map is uploaded.
            try:
                coords = _parse_boundary(raw_boundary)
                geographic = is_geographic(coords)
                fields["land"] = {"boundary": coords, "geographic": geographic,
                                  "metrics": describe(coords, geographic)}
            except (GeometryError, ValueError) as exc:
                flash(f"Boundary rejected: {exc}", "danger")
                return _render_edit(project, form=request.form)

        service.update_project(project["_id"], fields)
        flash("Project updated.", "success")
        return redirect(url_for("project.overview", project_id=project_id))

    return _render_edit(project)


@project_bp.get("/projects/<project_id>/map")
@project_access_required
def download_map(project_id: str):
    """Serve the project's linked map file back to a user who may open it."""
    project = request.project  # type: ignore[attr-defined]
    info = project.get("map") or {}
    path = _map_path(project["_id"], info)
    if path is None:
        abort(404)
    return send_file(path, as_attachment=True,
                     download_name=info.get("original_name") or path.name)


def _render_edit(project, form=None):
    return render_template(
        "project/edit.html",
        project=project,
        land_metrics=(project.get("land") or {}).get("metrics") or {},
        map_accept=mapfiles.ACCEPT,
        form=form,
    )


def _boundary_from_map(upload) -> tuple[str, bytes, list[list[float]], bool]:
    """Validate an uploaded map and read its boundary.

    Raises :class:`mapfiles.MapFileError` when the format is unsupported, the
    file is empty or it holds no closed outline.
    """
    kind = mapfiles.detect_kind(upload.filename)
    if kind is None:
        raise mapfiles.MapFileError(f"only {mapfiles.FORMATS} map files are accepted")
    data = upload.read()
    if not data:
        raise mapfiles.MapFileError("the uploaded file is empty")
    boundary, geographic = mapfiles.extract_boundary(kind, data)
    return kind, data, boundary, geographic


def _map_folder(project_id) -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]) / str(project_id)


def _map_path(project_id, info: dict) -> Path | None:
    """Stored path of the linked map, or ``None`` when it is missing."""
    stored = (info or {}).get("stored_name")
    if not stored:
        return None
    # Only the file name is used, so a hand-edited record can never escape the
    # project's own upload folder.
    path = _map_folder(project_id) / Path(str(stored)).name
    return path if path.is_file() else None


def _link_map(project_id, upload, kind: str, data: bytes) -> dict:
    """Write the uploaded map to disk and record it on the project.

    The file keeps a generated name so two projects can never collide, while the
    original name is preserved for the download.
    """
    folder = _map_folder(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.{kind}"
    (folder / stored).write_bytes(data)
    info = {
        "kind": kind,
        "original_name": secure_filename(upload.filename) or f"map.{kind}",
        "stored_name": stored,
        "size": len(data),
        "uploaded_at": datetime.now(timezone.utc),
    }
    service.update_project(project_id, {"map": info})
    service.record_map_file(project_id, info)
    return info


def _drop_map_files(project_id) -> None:
    """Remove a deleted project's uploaded map from disk."""
    folder = _map_folder(project_id)
    if not folder.is_dir():
        return
    for child in folder.iterdir():
        if child.is_file():
            child.unlink()
    folder.rmdir()


def _parse_boundary(raw: str) -> list[list[float]]:
    """Parse ``lon,lat lon,lat ...`` or ``lon,lat;lon,lat`` or JSON-ish lists."""
    text = raw.replace("\n", " ").replace(";", " ")
    pairs: list[list[float]] = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) != 2:
            raise ValueError(f"expected 'x,y' pairs, got '{token}'")
        pairs.append([float(parts[0]), float(parts[1])])
    if len(pairs) < 3:
        raise ValueError("at least 3 vertices are required")
    return pairs


@project_bp.get("/projects/<project_id>")
@project_access_required
def overview(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    return render_template(
        "project/overview.html",
        project=project,
        stats=service.stats(project["_id"]),
        land_metrics=(project.get("land") or {}).get("metrics") or {},
    )


@project_bp.post("/projects/<project_id>/settings")
@project_access_required
def update_settings(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    settings = project.get("settings") or {}

    def _float(field: str, default):
        raw = (request.form.get(field) or "").strip()
        try:
            return float(raw) if raw else default
        except ValueError:
            return default

    settings.update(
        {
            "sector_target_ha": _float("sector_target_ha", settings.get("sector_target_ha")),
            "zones_per_sector": int(_float("zones_per_sector",
                                           settings.get("zones_per_sector") or 3)),
            "pipe_diameters_mm": _float("pipe_diameters_mm", None) or settings.get("pipe_diameters_mm"),
            "dripper_flow_lph": _float("dripper_flow_lph", settings.get("dripper_flow_lph")),
            "dripper_spacing_m": _float("dripper_spacing_m", settings.get("dripper_spacing_m")),
            "lateral_spacing_m": _float("lateral_spacing_m", settings.get("lateral_spacing_m")),
        }
    )
    service.update_project(project["_id"], {"settings": settings})
    flash("Project settings saved.", "success")
    return redirect(url_for("project.overview", project_id=project_id))


@project_bp.post("/projects/<project_id>/rename")
@project_access_required
def rename_project(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("A project name is required.", "danger")
    else:
        service.update_project(project["_id"], {"name": name})
        flash("Project renamed.", "success")
    return redirect(url_for("project.overview", project_id=project_id))


@project_bp.post("/projects/<project_id>/delete")
@project_access_required
def delete_project(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    # ``owner_id`` is an ObjectId while ``current_user.id`` is a string, so the
    # two must be compared through ``service.can_delete``; comparing them
    # directly refused every owner who was not a full admin, even though the
    # project card offered them the Remove button.
    if not service.can_delete(current_user, project):
        flash("Only the owner or a full admin may delete a project.", "danger")
        return redirect(url_for("project.overview", project_id=project_id))

    deleted = service.delete_project(project["_id"])
    _drop_map_files(project["_id"])
    total = sum(deleted.values())
    flash(f"Project deleted along with {total} related records.", "success")
    return redirect(url_for("project.list_projects"))


@project_bp.post("/projects/<project_id>/members")
@project_access_required
def add_member(project_id: str):
    """Grant another account visibility of this project."""
    from ..models import User
    from ..utils.serialization import oid

    project = request.project  # type: ignore[attr-defined]
    username = (request.form.get("username") or "").strip()
    user = User.by_username(username)
    if user is None:
        flash("No such user.", "danger")
    elif user.id == str(project.get("owner_id")):
        flash("That account already owns this project.", "warning")
    else:
        members = project.get("members") or []
        uid = oid(user.id)
        if uid in members:
            flash("Already a member.", "warning")
        else:
            members.append(uid)
            service.update_project(project["_id"], {"members": members})
            flash(f"{user.username} added to the project.", "success")
    return redirect(url_for("project.overview", project_id=project_id))
