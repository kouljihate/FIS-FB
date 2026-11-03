"""Operational endpoints: liveness, readiness and the event feed.

Deliberately outside ``/api`` and free of login: an orchestrator (Docker
compose, Kubernetes, a load balancer) must be able to probe them, and they
expose no project data.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

ops_bp = Blueprint("ops", __name__)


def _service():
    return current_app.config.get("SERVICE_NAME") or "fis-web"


@ops_bp.route("/healthz")
def healthz():
    """Liveness: the process is up and can render a response."""
    from irrigation import __version__

    return jsonify({"status": "ok", "service": _service(), "version": __version__})


@ops_bp.route("/readyz")
def readyz():
    """Readiness: the database answers.  A silent broker is reported, not fatal.

    The web app stays usable without RabbitMQ (events queue and are dropped),
    so the broker is a *check*, not a gate; MongoDB is the gate.
    """
    from irrigation import database_ready
    from irrigation.events import status as events_status

    database_ok = bool(database_ready())
    events = events_status()
    return jsonify({
        "status": "ready" if database_ok else "degraded",
        "service": _service(),
        "checks": {
            "mongo": {"ok": database_ok},
            "events": events,
        },
    }), (200 if database_ok else 503)


@ops_bp.route("/ops/events")
@login_required
def recent_events():
    """The last audited domain events (admins only) - the audit service's output."""
    if not getattr(current_user, "is_admin", False):
        return jsonify({"error": "forbidden"}), 403

    from irrigation.extensions import mongo
    from irrigation.schema import real_database

    try:
        limit = min(max(int(request.args.get("limit", 25)), 1), 200)
    except (TypeError, ValueError):
        limit = 25

    query = {}
    project_id = request.args.get("project_id", type=int)
    if project_id is not None:
        query["project_id"] = project_id

    try:
        db = real_database(mongo)
        documents = list(db.event_log.find(query, {"_id": 0})
                         .sort("occurred_at", -1).limit(limit))
    except Exception as exc:  # database down: report, do not 500
        return jsonify({"error": str(exc)}), 503

    for doc in documents:
        for field in ("occurred_at", "received_at"):
            value = doc.get(field)
            if hasattr(value, "isoformat"):
                doc[field] = value.isoformat()

    from irrigation.events import status as events_status

    return jsonify({"count": len(documents), "publisher": events_status(),
                    "events": documents})
