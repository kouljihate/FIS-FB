import os
from flask import Flask, render_template
from irrigation.extensions import mongo, login_manager
from irrigation.utils.logger import setup_logging

__version__ = '0.3.82'


def create_app():
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object('config.Config')

    os.makedirs(app.config.get('UPLOAD_FOLDER', 'uploads'), exist_ok=True)

    # Extensions
    mongo.init_app(app)
    login_manager.init_app(app)

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
    app.register_blueprint(trees_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(engineering_bp, url_prefix='/engineering')
    app.register_blueprint(estimation_bp, url_prefix='/estimation')
    app.register_blueprint(water_bp, url_prefix='/water')

    # Error handlers
    @app.context_processor
    def inject_version():
        return {'app_version': __version__}

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('errors/500.html'), 500

    return app
