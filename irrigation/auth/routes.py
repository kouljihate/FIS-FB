"""Authentication routes.

Passwords are hashed by :mod:`werkzeug.security`.  The first account created
becomes a ``full_admin`` so a fresh deployment is usable without shipping a
default password in the source tree; every later self-registration is a
``customer``.  Only a ``full_admin`` may promote somebody.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ..constants import PROFILE_CUSTOMER, PROFILE_FULL_ADMIN, PROFILE_LABELS, PROFILES
from ..models import User
from ..utils.i18n import SUPPORTED
from ..utils.decorators import role_required

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _redirect_target(default_endpoint: str = "project.list_projects") -> str:
    target = request.args.get("next") or request.form.get("next")
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for(default_endpoint)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("project.list_projects"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.by_username(username)
        if user is None or not user.check_password(password):
            flash("Invalid username or password.", "danger")
        elif not user.is_active:
            flash("This account is disabled.", "danger")
        else:
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(_redirect_target())

    return render_template("auth/login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("project.list_projects"))

    first_account = User.count() == 0
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        min_length = current_app.config["PASSWORD_MIN_LENGTH"]

        error = None
        if len(username) < 3:
            error = "Username must be at least 3 characters."
        elif User.by_username(username):
            error = "That username is already taken."
        elif len(password) < min_length:
            error = f"Password must be at least {min_length} characters."
        elif password != confirm:
            error = "The two passwords do not match."

        if error:
            flash(error, "danger")
        else:
            role = PROFILE_FULL_ADMIN if first_account else PROFILE_CUSTOMER
            user = User.create(username, password, email=email, role=role,
                               full_name=full_name)
            login_user(user)
            flash("Account created.", "success")
            return redirect(url_for("project.list_projects"))

    return render_template("auth/register.html", first_account=first_account)


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        action = request.form.get("action", "password")

        if action == "password":
            current = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""
            min_length = current_app.config["PASSWORD_MIN_LENGTH"]
            if not current_user.check_password(current):
                flash("Current password is incorrect.", "danger")
            elif len(new) < min_length:
                flash(f"Password must be at least {min_length} characters.", "danger")
            elif new != confirm:
                flash("The two passwords do not match.", "danger")
            else:
                current_user.set_password(new)
                flash("Password updated.", "success")

        elif action == "role" and current_user.is_superuser:
            target_id = request.form.get("user_id") or ""
            role = request.form.get("role") or ""
            target = User.get(target_id)
            if target is None:
                flash("User not found.", "danger")
            elif role not in PROFILES:
                flash("Unknown profile.", "danger")
            elif target.id == current_user.id:
                flash("You cannot change your own profile.", "warning")
            else:
                target.set_role(role)
                flash(f"{target.username} is now {PROFILE_LABELS[role]['en']}.", "success")

        return redirect(url_for("auth.profile"))

    users = (
        [User(doc) for doc in User.collection().find().sort("username", 1)]
        if current_user.is_superuser else []
    )
    return render_template("auth/profile.html", users=users,
                           profiles=PROFILES, profile_labels=PROFILE_LABELS)


@auth_bp.post("/language")
def set_language():
    lang = (request.form.get("lang") or "").strip()
    if lang in SUPPORTED:
        session["lang"] = lang
    return redirect(request.form.get("next") or url_for("project.list_projects"))
