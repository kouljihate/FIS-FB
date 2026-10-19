"""Access-control decorators."""

from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required


def wants_json() -> bool:
    return request.path.startswith("/api/") or "application/json" in (
        request.headers.get("Accept") or ""
    )


def role_required(*roles: str) -> Callable:
    """Gate a view behind authentication *and* one of ``roles``."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if not current_user.has_role(*roles):
                if wants_json():
                    return jsonify({"error": "forbidden", "required_roles": list(roles)}), 403
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def project_access_required(view: Callable) -> Callable:
    """Ensure the ``project_id`` in the URL exists and is visible to the user.

    The resolved project document is stored on ``request.project`` so the view
    (and the template) can use it without a second query.
    """

    from ..project.service import can_access, get_project

    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        project_id = kwargs.get("project_id")
        project = get_project(project_id)
        if project is None or not can_access(current_user, project):
            if wants_json():
                return jsonify({"error": "not_found"}), 404
            flash("Project not found.", "danger")
            return redirect(url_for("project.list_projects"))
        request.project = project  # type: ignore[attr-defined]
        return view(*args, **kwargs)

    return wrapper
