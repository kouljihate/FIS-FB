"""Gunicorn configuration for the FIS web service.

Values come from the environment so the same image can be tuned per host:

    WEB_CONCURRENCY   gunicorn workers        (default: 4, capped at 4)
    GUNICORN_THREADS  threads per worker      (default: 4)
    GUNICORN_TIMEOUT  request timeout, seconds (default: 120)
    GUNICORN_BIND     listen address          (default: 0.0.0.0:8000)

``preload_app`` stays off: each worker creates its own MongoClient and event
publisher *after* the fork, so no socket is shared across processes.
"""

import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY") or min(4, os.cpu_count() or 2))
threads = int(os.environ.get("GUNICORN_THREADS") or 4)
timeout = int(os.environ.get("GUNICORN_TIMEOUT") or 120)
graceful_timeout = 30
keepalive = 5

# Geometry, elevation and piping generation are CPU heavy: a worker quietly
# recycling in the middle of one is worse than a slow request.
preload_app = False
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS") or 2000)
max_requests_jitter = 200

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")
