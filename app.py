"""Entry point: ``python app.py`` starts the development server.

Access the application at http://127.0.0.1:5000
"""

from __future__ import annotations

from irrigation import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=app.config["RUN_HOST"],
        port=app.config["RUN_PORT"],
        debug=app.config["DEBUG"],
    )
