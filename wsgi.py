"""WSGI entry point used by gunicorn in the container image.

    gunicorn --config docker/gunicorn.conf.py wsgi:app

``app.py`` remains the interactive development entry point; this module has no
side effects beyond building the application, so the process manager stays in
charge of workers and signals.
"""

from irrigation import create_app

app = create_app()
