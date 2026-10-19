"""Application factory for the Irrigation Planning System (FIS)."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, redirect

from config import get_config

from .constants import APP_VERSION, MAIN_TABS, PROFILES, PROFILE_LABELS
from .extensions import login_manager, mongo
from .models import User

__all__ = ["create_app"]


def _configure_logging(app: Flask) -> None:
    log_dir = Path(app.config["LOG_FOLDER"])
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "irrigation.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO if not app.debug else logging.DEBUG)


def _ensure_directories(app: Flask) -> None:
    for key in ("UPLOAD_FOLDER", "EXPORT_FOLDER", "IMPORT_FOLDER"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)


def _register_blueprints(app: Flask) -> None:
    # Imported here so that importing :mod:`irrigation` never has import-time
    # side effects on the Flask application object.
    from .api.routes import api_bp
    from .auth.routes import auth_bp
    from .estimation.routes import estimation_bp
    from .geometry.routes import geometry_bp
    from .hydrology.routes import hydrology_bp
    from .hydrology2.routes import hydrology2_bp
    from .project.routes import project_bp
    from .report.routes import report_bp
    from .trees.routes import trees_bp
    from .water_resource.routes import water_resource_bp

    for bp in (
        auth_bp,
        project_bp,
        water_resource_bp,
        geometry_bp,
        hydrology_bp,
        trees_bp,
        hydrology2_bp,
        estimation_bp,
        report_bp,
        api_bp,
    ):
        app.register_blueprint(bp)


def _register_error_handlers(app: Flask) -> None:
    from flask import render_template, request

    def wants_json() -> bool:
        return request.path.startswith("/api/") or "application/json" in (
            request.headers.get("Accept") or ""
        )

    @app.errorhandler(401)
    def unauthorized(err):  # pragma: no cover - exercised via routes
        if wants_json():
            return jsonify({"error": "unauthorized"}), 401
        return render_template("errors/error.html", code=401,
                               message="Authentication required."), 401

    @app.errorhandler(403)
    def forbidden(err):  # pragma: no cover
        if wants_json():
            return jsonify({"error": "forbidden"}), 403
        return render_template("errors/error.html", code=403,
                              message="You do not have access to this page."), 403

    @app.errorhandler(404)
    def not_found(err):  # pragma: no cover
        if wants_json():
            return jsonify({"error": "not_found"}), 404
        return render_template("errors/error.html", code=404,
                               message="Page not found."), 404

    @app.errorhandler(413)
    def too_large(err):  # pragma: no cover - depends on a large upload
        # A map upload past MAX_CONTENT_LENGTH is refused before the view runs.
        if wants_json():
            return jsonify({"error": "payload_too_large"}), 413
        return render_template(
            "errors/error.html", code=413,
            message="The uploaded map is larger than the 32 MiB limit."), 413

    @app.errorhandler(500)
    def server_error(err):  # pragma: no cover
        app.logger.exception("unhandled application error")
        if wants_json():
            return jsonify({"error": "internal_error"}), 500
        return render_template("errors/error.html", code=500,
                               message="Internal server error."), 500


def _register_context(app: Flask) -> None:
    #: Which main tab a blueprint belongs to; drives the active highlight.
    blueprint_tabs = {
        "project": "project",
        "water_resource": "water_resource",
        "geometry": "geometry",
        "hydrology": "hydrology1",
        "trees": "trees",
        "hydrology2": "hydrology2",
        "estimation": "estimation",
        "report": "report",
    }

    @app.context_processor
    def inject_globals():
        from flask import request, session

        from .utils.i18n import SUPPORTED, label_ar, translate

        lang = session.get("lang", app.config["DEFAULT_LANGUAGE"])
        if lang not in SUPPORTED:
            lang = "en"
        return {
            "main_tabs": MAIN_TABS,
            "profiles": PROFILES,
            "profile_labels": PROFILE_LABELS,
            "t": lambda key: translate(key, lang),
            # ``t_en`` / ``t_ar`` always answer in that language: the chrome is
            # bilingual, so both labels are printed on every screen.
            "t_en": lambda key: translate(key, "en"),
            "t_ar": lambda key: translate(key, "ar"),
            # ``tar`` translates a label that came from a route module (table
            # column or form field); unknown labels come back unchanged.
            "tar": label_ar,
            "lang": lang,
            "is_rtl": lang == "ar",
            "app_version": APP_VERSION,
            "active_tab_key": blueprint_tabs.get(request.blueprint or "", ""),
        }


def create_app(config_name: str | None = None) -> Flask:
    """Build a fully configured Flask application.

    Static assets live in the project-level ``static/`` folder, which holds the
    vendored Bootstrap/Bootswatch theme, the Arabic and Latin fonts and the
    application stylesheet; ``template_folder`` stays inside the package.
    """
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder=str(static_dir),
        static_url_path="/static",
    )
    app.config.from_object(get_config(config_name))

    _ensure_directories(app)
    _configure_logging(app)

    mongo.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."

    @login_manager.user_loader
    def load_user(user_id: str):
        return User.get(user_id)

    @login_manager.unauthorized_handler
    def unauthorized():
        """Answer API callers with 401 instead of an HTML login redirect.

        Flask-Login's default is a 302 to the login page, which would make a
        programmatic client follow a redirect and receive an HTML page where it
        expects JSON.
        """
        from flask import jsonify, request, url_for

        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("auth.login", next=request.full_path))

    _register_blueprints(app)
    _register_error_handlers(app)
    _register_context(app)

    return app
