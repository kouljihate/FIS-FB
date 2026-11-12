import os
import time
from flask import Flask, current_app, g, render_template, request
from pymongo import MongoClient
from irrigation.extensions import mongo, login_manager
from irrigation.utils.logger import setup_logging

__version__ = '0.4.80'

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


#: The footer polls the database on every page render, and the app's own client
#: waits 30 s for an unreachable server, which would stall the page.  The
#: indicator therefore probes through a dedicated client with a 1 s budget and
#: caches its verdict for a few seconds.
_DB_PROBE = {'client': None, 'uri': None}
_DB_STATUS = {'ready': True, 'checked_at': 0.0}
_DB_STATUS_TTL = 5.0


def database_ready():
    """True when MongoDB answers a ping; the answer is cached briefly."""
    now = time.monotonic()
    if now - _DB_STATUS['checked_at'] < _DB_STATUS_TTL:
        return _DB_STATUS['ready']

    ready = False
    try:
        uri = current_app.config.get('MONGO_URI')
        if _DB_PROBE['client'] is None or _DB_PROBE['uri'] != uri:
            if _DB_PROBE['client'] is not None:
                _DB_PROBE['client'].close()
            _DB_PROBE['client'] = MongoClient(uri, serverSelectionTimeoutMS=1000)
            _DB_PROBE['uri'] = uri
        _DB_PROBE['client'].admin.command('ping')
        ready = True
    except Exception:
        # Unreachable cluster, bad credentials or no app context: show "Not".
        pass

    _DB_STATUS.update(ready=ready, checked_at=now)
    return ready


def create_app():
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object('config.Config')

    os.makedirs(app.config.get('UPLOAD_FOLDER', 'uploads'), exist_ok=True)

    # Extensions: one pool per worker process, with a short selection timeout
    # so an unreachable database fails fast instead of hanging the request.
    mongo.init_app(app,
                   maxPoolSize=app.config.get('MONGO_MAX_POOL_SIZE', 50),
                   serverSelectionTimeoutMS=app.config.get('MONGO_TIMEOUT_MS', 5000))
    login_manager.init_app(app)

    # Domain events are published *after* the request's write is committed:
    # this teardown handler is registered before install_storage() below,
    # and Flask unwinds teardown handlers in reverse registration order, so
    # the embedded-project flush runs first.
    from irrigation.events import flush as flush_domain_events
    from irrigation.events import shutdown as shutdown_domain_events

    @app.teardown_appcontext
    def _publish_domain_events(_exc):
        flush_domain_events()

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

    # Event bus (RabbitMQ).  The publisher connects lazily from its own
    # thread, so an absent broker never delays startup.
    import atexit

    from irrigation.events import init_events
    init_events(app)
    atexit.register(shutdown_domain_events)

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
    from irrigation.report.routes import report_bp
    from irrigation.ops.routes import ops_bp

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
    app.register_blueprint(report_bp, url_prefix='/report')
    # /healthz, /readyz and /ops/events: no prefix, no login, no project data.
    app.register_blueprint(ops_bp)

    @app.after_request
    def _cache_static_assets(response):
        # Static files are versioned by the URL the templates build, so a
        # container deployment can cache them aggressively instead of
        # revalidating every CSS/JS/image on each page.
        if response.status_code == 200 and request.path.startswith('/static/'):
            response.headers.setdefault(
                'Cache-Control', 'public, max-age=%d' %
                int(app.config.get('STATIC_CACHE_SECONDS') or 3600))
        return response

    # A Project Owner reads their project through Map Overview, Estimation and
    # Report only: the tabs that build it refuse a typed URL too.  Registered
    # after the blueprints so the temporary-password redirect runs first.
    from irrigation.access import enforce_owner_scope
    app.before_request(enforce_owner_scope)

    # Error handlers
    repo = repo_name()

    @app.context_processor
    def inject_version():
        # The Pipes sub-nav needs to know whether the drip line stage is open:
        # it is built row by row, so it waits for validated tree rows.
        from irrigation.access import rows_validated
        view_args = request.view_args or {}
        return {
            'app_version': __version__,
            'app_repo': repo,
            'db_ready': database_ready(),
            'workflow_rows_validated': rows_validated(view_args.get('project_id')),
            'ui_page': _current_page(),
        }

    def _current_page():
        """ui_registry entry of the page being rendered (footer badge)."""
        # The error handlers stamp the key they render; an URL that matched no
        # route has no endpoint and falls back to the 404 page.
        key = getattr(g, 'ui_registry_key', None) or request.endpoint or 'error:404'
        try:
            return page_document(real_database(mongo), key)
        except Exception:
            return None

    @app.errorhandler(404)
    def not_found(e):
        g.ui_registry_key = 'error:404'
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        g.ui_registry_key = 'error:500'
        return render_template('errors/500.html'), 500

    # The UI registry mirrors the code: every page (a GET view that renders a
    # template) and every modal form found in the templates it renders is
    # reconciled into the ui_registry collection here, so a page or modal added
    # or removed shows up on the next start without a manual step.
    from irrigation.ui_registry import page_document, sync_ui_registry
    try:
        registry_summary = sync_ui_registry(app, real_database(mongo))
        app.logger.info(
            "UI registry: %d pages, %d modal forms (%d added, %d removed)",
            registry_summary['pages'], registry_summary['modals'],
            len(registry_summary['added']), len(registry_summary['removed']))
    except Exception:
        app.logger.exception("UI registry sync failed")

    return app
