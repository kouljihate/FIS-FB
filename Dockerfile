# FIS web application image.
#
# The same image runs both services in docker-compose.yml: the Flask app under
# gunicorn (default CMD) and the event consumer worker (command override).
# Everything scientific (shapely, pyproj, numpy, lxml) ships manylinux wheels,
# so no compiler toolchain is needed in the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is only for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 fis \
    && mkdir -p /app/uploads /app/logs \
    && chown -R fis:fis /app
USER fis

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# gunicorn (not the Flask dev server): see docker/gunicorn.conf.py for workers.
CMD ["gunicorn", "--config", "docker/gunicorn.conf.py", "wsgi:app"]
