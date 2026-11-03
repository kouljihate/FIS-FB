"""Domain events for FIS.

Producers call :func:`emit` inside a request; the envelopes are published by a
background publisher after the request has committed (the embedded-project
storage flushes at teardown, and this teardown callback runs after that one).
Outside a request (CLI, worker) :func:`emit` publishes immediately.

    from irrigation.events import emit, catalog

    emit(catalog.SECTOR_CREATED, {"sector_id": 12}, project_id=project_id)
"""

from __future__ import annotations

import logging
import threading

from flask import current_app, g, has_app_context, has_request_context

from irrigation.events import catalog
from irrigation.events.bus import ENVELOPE_VERSION, Publisher, build_envelope, mask_url

__all__ = [
    "catalog", "emit", "flush", "publish", "init_events", "shutdown",
    "publisher", "status", "build_envelope", "Publisher", "ENVELOPE_VERSION",
]

log = logging.getLogger(__name__)

_PENDING_ATTR = "_fis_pending_events"

_publisher = None
_lock = threading.Lock()


def init_events(app):
    """Create and start the publisher described by the app config."""
    global _publisher
    if not app.config.get("EVENTS_ENABLED", True):
        app.logger.info("Domain events disabled (EVENTS_ENABLED=0)")
        return None

    with _lock:
        if _publisher is not None:
            return _publisher
        _publisher = Publisher(
            app.config.get("RABBITMQ_URL"),
            app.config.get("EVENTS_EXCHANGE") or "fis.events",
            service=app.config.get("SERVICE_NAME") or "fis-web",
            max_queue=int(app.config.get("EVENTS_QUEUE_MAX") or 2000),
            connect_timeout=float(app.config.get("EVENTS_CONNECT_TIMEOUT") or 3),
        ).start()
        app.logger.info("Domain events enabled -> %s (exchange=%s, service=%s)",
                        mask_url(app.config.get("RABBITMQ_URL")),
                        _publisher.exchange, _publisher.service)
    return _publisher


def publisher():
    """The process-wide publisher, or ``None`` when events are disabled."""
    return _publisher


def _source():
    if has_app_context():
        return current_app.config.get("SERVICE_NAME") or "fis-web"
    return "fis-cli"


def _actor_id():
    """The signed-in user's id, when the event is emitted from a request."""
    try:
        from flask_login import current_user

        if getattr(current_user, "is_authenticated", False):
            return str(getattr(current_user, "id", "") or "") or None
    except Exception:  # no login manager in this context
        return None
    return None


def emit(event_type, payload=None, *, project_id=None, actor_id=None,
         correlation_id=None):
    """Record a domain event.

    Inside a request the event is deferred and published after the write is
    flushed; anywhere else (CLI, worker, app context without a request) it is
    queued for immediate delivery.  Returns the envelope so a caller can log
    its ``event_id``.
    """
    envelope = build_envelope(
        event_type,
        payload,
        project_id=project_id,
        actor_id=actor_id or _actor_id(),
        source=_source(),
        correlation_id=correlation_id,
    )
    if has_request_context():
        pending = getattr(g, _PENDING_ATTR, None)
        if pending is None:
            pending = []
            setattr(g, _PENDING_ATTR, pending)
        pending.append(envelope)
        return envelope
    publish(envelope)
    return envelope


def publish(envelope) -> bool:
    """Hand one envelope to the publisher (False when events are off)."""
    if _publisher is None:
        return False
    return _publisher.publish(envelope)


def flush() -> int:
    """Publish the events deferred by this request.  Never raises."""
    if not has_app_context():
        return 0
    pending = getattr(g, _PENDING_ATTR, None)
    if not pending:
        return 0
    setattr(g, _PENDING_ATTR, [])
    published = 0
    for envelope in pending:
        try:
            if publish(envelope):
                published += 1
        except Exception:  # a broker problem must not surface as a 500
            log.exception("Could not publish %s event", envelope.get("type"))
    return published


def status():
    """Publisher counters for ``/readyz``; always JSON-serialisable."""
    if _publisher is None:
        return {"enabled": False}
    return _publisher.status()


def shutdown():
    """Stop the publisher thread (tests, CLI one-shots, worker shutdown)."""
    global _publisher
    with _lock:
        if _publisher is not None:
            _publisher.close()
            _publisher = None
