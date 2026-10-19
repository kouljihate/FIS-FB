"""Role rules for the two-collection model.

Profiles
--------
Admin
    Everything: every project and the user administration page.
Project Admin
    Every project (create, edit, delete), but no user administration.
Project Owner
    Only the projects they own.
"""

from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user

from irrigation.extensions import mongo

ROLE_ADMIN = "Admin"
ROLE_PROJECT_ADMIN = "Project Admin"
ROLE_PROJECT_OWNER = "Project Owner"
ROLES = (ROLE_ADMIN, ROLE_PROJECT_ADMIN, ROLE_PROJECT_OWNER)


def role_of(user=None):
    user = user or current_user
    role = getattr(user, "profile", None) or getattr(user, "role", None)
    return role if role in ROLES else ROLE_PROJECT_OWNER


def is_admin(user=None):
    return role_of(user) == ROLE_ADMIN


def sees_all_projects(user=None):
    return role_of(user) in (ROLE_ADMIN, ROLE_PROJECT_ADMIN)


def project_query(user=None):
    """Mongo filter that limits projects to what the user may open."""
    if sees_all_projects(user):
        return {}
    return {"user_id": int((user or current_user).id)}


def find_project(project_id, user=None):
    query = {"id": int(project_id)}
    query.update(project_query(user))
    return mongo.db.projects.find_one(query)


def can_manage_project(project, user=None):
    if not project:
        return False
    if sees_all_projects(user):
        return True
    return project.get("user_id") == int((user or current_user).id)


def require_project(project_id, user=None):
    """Project the user may open, or ``None`` after a flash."""
    project = find_project(project_id, user)
    if not project:
        flash("Project not found / المشروع غير موجود", "danger")
    return project


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not is_admin():
            flash("Admins only / للمسؤولين فقط", "danger")
            return redirect(url_for("project.index"))
        return view(*args, **kwargs)

    return wrapped
