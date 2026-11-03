"""Event handlers: the logic that runs *outside* the web request.

Each handler is a plain callable ``handler(db, envelope) -> bool`` so it can be
unit-tested without a broker.  The worker dispatches every event to the
handlers registered for its queue.

The first extracted service is the **audit service**: it keeps an append-only
``event_log`` collection, which is also what makes replays and debugging of a
multi-service deployment possible.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

log = logging.getLogger(__name__)


def ensure_event_indexes(db):
    """Idempotent indexes; ``event_id`` unique is the de-duplication guarantee."""
    db.event_log.create_index("event_id", unique=True, name="event_id_unique")
    db.event_log.create_index([("occurred_at", -1)], name="occurred_at_desc")
    db.event_log.create_index([("project_id", 1), ("occurred_at", -1)],
                              name="project_timeline")
    db.event_log.create_index([("type", 1), ("occurred_at", -1)],
                              name="type_timeline")


def audit(db, envelope) -> bool:
    """Append the event to ``event_log``.  False when it was a duplicate."""
    doc = dict(envelope)
    doc["received_at"] = datetime.now(timezone.utc)
    try:
        db.event_log.insert_one(doc)
    except DuplicateKeyError:
        # At-least-once delivery: the same event arrived twice.
        log.debug("Duplicate event %s ignored", envelope.get("event_id"))
        return False
    log.info("audit: %-22s project=%s actor=%s", envelope.get("type"),
             envelope.get("project_id"), envelope.get("actor_id"))
    return True


#: queue name -> handlers applied to every message on that queue.
HANDLERS = {
    "fis.audit": (audit,),
}
