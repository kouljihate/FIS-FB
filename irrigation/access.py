"""Role rules for the two-collection model.

Profiles
--------
Admin
    Everything: every project and the user administration page.
Project Admin
    Every project (create, edit, delete), but no user administration.
Project Owner
    Only the projects they own, and read-only: the project is set up for them,
    so they walk through Map Overview, Estimation and Report and cannot create,
    edit or remove a project (or open the sections that build it).
"""

from functools import wraps

from flask import flash, redirect, request, url_for
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


def can_edit_projects(user=None):
    """Only an Admin or a Project Admin creates, edits or removes a project."""
    return sees_all_projects(user)


#: Endpoints a Project Owner may reach.  Their project is prepared by the
#: Project Admin, so the owner only reads it through the tabs below; every
#: endpoint listed here is either read-only or guarded on its own.
OWNER_ENDPOINT_PREFIXES = ('static', 'auth.', 'api.', 'overview.', 'estimation.',
                           'project.', 'report.')
OWNER_ENDPOINTS = frozenset({
    'engineering.report_view',
    'engineering.technical_report_view',
    'engineering.report',
    'engineering.elevation_heatmap',
})


def owner_can_reach(endpoint):
    """True when a Project Owner may open ``endpoint``."""
    if not endpoint:
        return True
    if endpoint in OWNER_ENDPOINTS:
        return True
    return any(endpoint == prefix or endpoint.startswith(prefix)
               for prefix in OWNER_ENDPOINT_PREFIXES)


def enforce_owner_scope():
    """Keep a Project Owner out of the tabs that build the project.

    The workflow navigation already hides them, but the pages must refuse a
    typed URL as well, so the rule lives here instead of in the templates.
    """
    if not getattr(current_user, 'is_authenticated', False):
        return None
    if sees_all_projects():
        return None
    if owner_can_reach(request.endpoint):
        return None

    flash('This section is not available for a Project Owner / '
          'هذا القسم غير متاح لمالك المشروع', 'warning')
    project_id = (request.view_args or {}).get('project_id')
    if project_id:
        return redirect(url_for('overview.index', project_id=project_id))
    return redirect(url_for('project.index'))


def project_query(user=None):
    """Mongo filter that limits projects to what the user may open.

    A Project Owner sees the projects linked to their account
    (``owner_user_id``) and the ones they created themselves (``user_id``);
    a Project Admin usually creates a project on behalf of the landowner, so
    the link, not the creator, is what gives the owner access.
    """
    user = user or current_user
    if sees_all_projects(user):
        return {}
    uid = int(user.id)
    return {"$or": [{"user_id": uid}, {"owner_user_id": uid}]}


def find_project(project_id, user=None):
    query = {"id": int(project_id)}
    query.update(project_query(user))
    return mongo.db.projects.find_one(query)


def can_manage_project(project, user=None):
    if not project:
        return False
    user = user or current_user
    if sees_all_projects(user):
        return True
    uid = int(user.id)
    return uid in (project.get("user_id"), project.get("owner_user_id"))


def require_project(project_id, user=None):
    """Project the user may open, or ``None`` after a flash."""
    project = find_project(project_id, user)
    if not project:
        flash("Project not found / المشروع غير موجود", "danger")
    return project


def rows_validated(project_id):
    """True when the project has tree rows and every one is validated.

    The DripLine stage is built row by row, so it stays locked until the Rows
    tab has been validated for the whole project.
    """
    if not project_id:
        return False
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return False
    rows = mongo.db.tree_rows
    if not rows.count_documents({"project_id": project_id}):
        return False
    return rows.count_documents({
        "project_id": project_id,
        "validated": {"$ne": True},
    }) == 0


def editor_required(view):
    """Refuse a project create/edit/remove to a Project Owner."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not can_edit_projects():
            flash('A Project Owner cannot create, edit or remove a project / '
                  'لا يمكن لمالك المشروع إنشاء أو تعديل أو حذف مشروع', 'danger')
            return redirect(url_for('project.index'))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not is_admin():
            flash("Admins only / للمسؤولين فقط", "danger")
            return redirect(url_for("project.index"))
        return view(*args, **kwargs)

    return wrapped
