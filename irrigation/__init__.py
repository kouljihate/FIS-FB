import os
from flask import Flask, render_template
from irrigation.extensions import mongo, login_manager
from irrigation.utils.logger import setup_logging

__version__ = '0.4.13'

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_name_from_url(url):
    """``git@github.com:owner/repo.git`` / ``https://host/owner/repo`` -> ``repo``."""
    if not url:
        return None
    tail = url.strip().rstrip('/').replace('\\', '/').rsplit('/', 1)[-1]
    tail = tail.rsplit(':', 1)[-1]
    if tail.endswith('.git'):
        tail = tail[:-4]
    return tail or None


def _git_origin_url(root):
    """Read ``remote.origin.url`` from .git/config (no subprocess, no network)."""
    git_path = os.path.join(root, '.git')
    if os.path.isfile(git_path):
        # Worktrees and submodules keep a pointer file instead of a directory.
        try:
            with open(git_path, encoding='utf-8', errors='replace') as handle:
                pointer = handle.readline().strip()
        except OSError:
            return None
        if not pointer.startswith('gitdir:'):
            return None
        git_path = os.path.normpath(os.path.join(root, pointer.split(':', 1)[1].strip()))
    try:
        with open(os.path.join(git_path, 'config'), encoding='utf-8', errors='replace') as handle:
            section = ''
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith(('#', ';')):
                    continue
                if line.startswith('[') and line.endswith(']'):
                    section = line[1:-1].strip()
                    continue
                if section == 'remote "origin"' and line.lower().startswith('url'):
                    return line.split('=', 1)[1].strip()
    except OSError:
        return None
    return None


def repo_name():
    """Repository name for the footer badge: never a path, never the URL."""
    override = os.environ.get('APP_REPO', '').strip()
    if override:
        return _repo_name_from_url(override) or override
    detected = _repo_name_from_url(_git_origin_url(PROJECT_ROOT))
    return detected or os.path.basename(PROJECT_ROOT) or 'FIS'


def create_app():
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object('config.Config')

    os.makedirs(app.config.get('UPLOAD_FOLDER', 'uploads'), exist_ok=True)

    # Extensions
    mongo.init_app(app)
    login_manager.init_app(app)

    # The new model stores everything in two collections (user / project).
    # Wrap mongo.db so existing collection-style queries keep working.
    from irrigation.storage import install_storage
    install_storage(mongo, app)

    from irrigation.schema import ensure_schema, real_database
    try:
        ensure_schema(real_database(mongo))
    except Exception:
        app.logger.exception("Database schema setup failed")

    # Logging
    setup_logging(app)
    app.logger.info("Irrigation Planner starting up...")

    # User loader for Flask-Login
    from irrigation.models import serialize_doc

    @login_manager.user_loader
    def load_user(user_id):
        from irrigation.auth.user import User
        user_doc = mongo.db.users.find_one({'id': int(user_id)})
        if user_doc:
            return User(user_doc)
        return None

    # Register blueprints
    from irrigation.auth.routes import auth_bp
    from irrigation.project.routes import project_bp
    from irrigation.geometry.routes import geometry_bp
    from irrigation.hydrology.routes import hydrology_bp
    from irrigation.components.routes import components_bp
    from irrigation.overview.routes import overview_bp
    from irrigation.trees.routes import trees_bp
    from irrigation.api.routes import api_bp
    from irrigation.engineering.routes import engineering_bp
    from irrigation.estimation.routes import estimation_bp
    from irrigation.water.routes import water_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(geometry_bp)
    app.register_blueprint(hydrology_bp)
    app.register_blueprint(components_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(trees_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(engineering_bp, url_prefix='/engineering')
    app.register_blueprint(estimation_bp, url_prefix='/estimation')
    app.register_blueprint(water_bp, url_prefix='/water')

    # Error handlers
    repo = repo_name()

    @app.context_processor
    def inject_version():
        return {'app_version': __version__, 'app_repo': repo}

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('errors/500.html'), 500

    return app
