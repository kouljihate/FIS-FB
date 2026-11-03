"""RabbitMQ publisher and event envelope.

Design rules the web app depends on:

* **A request never fails because of the broker.**  ``emit`` only appends to
  the request's pending list; a background thread does the network I/O.
* **A broker outage does not block a request.**  The queue is bounded, so a
  long outage costs dropped events (counted and logged), never latency.
* **Publishing is at-least-once.**  Publisher confirms are enabled and every
  envelope carries a ``message_id``; consumers de-duplicate on ``event_id``.
* **The broker is optional.**  Without ``pika`` or with ``EVENTS_ENABLED=0``
  the publisher stays idle and the app behaves exactly as before.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

#: Bumped when the envelope shape changes incompatibly.
ENVELOPE_VERSION = 1

log = logging.getLogger(__name__)


def build_envelope(event_type, payload=None, *, project_id=None, actor_id=None,
                   source="fis-web", correlation_id=None):
    """A self-describing message: a consumer needs no other context."""
    return {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "version": ENVELOPE_VERSION,
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "source": source,
        "project_id": project_id,
        "actor_id": actor_id,
        "correlation_id": correlation_id,
        "payload": payload or {},
    }


def mask_url(url: str) -> str:
    """``amqp://user:secret@host`` -> ``amqp://user:***@host`` (for logs)."""
    if not url:
        return ""
    scheme, sep, rest = url.partition("://")
    if not sep or "@" not in rest:
        return url
    credentials, at, host = rest.rpartition("@")
    user, colon, _password = credentials.partition(":")
    if colon:
        return "%s://%s:***%s%s" % (scheme, user, at, host)
    return url


class Publisher:
    """Bounded, thread-backed publisher with lazy connect and reconnect."""

    def __init__(self, url, exchange, *, service="fis-web", max_queue=2000,
                 connect_timeout=3.0, retry_delay=1.0, max_attempts=3):
        self.url = url
        self.exchange = exchange
        self.service = service
        self._queue: "queue.Queue[dict | None]" = queue.Queue(maxsize=max_queue)
        self._connect_timeout = connect_timeout
        self._retry_delay = retry_delay
        self._max_attempts = max_attempts
        self._connection = None
        self._channel = None
        self._thread = None
        self._closing = threading.Event()
        self._lock = threading.Lock()

        #: Counters exposed by ``/readyz`` and the worker's log line.
        self.published = 0
        self.failed = 0
        self.dropped = 0
        self.reconnects = 0
        self.connected = False
        self.last_error = None

    # -- lifecycle --------------------------------------------------------- #
    def start(self):
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name="fis-events",
                                        daemon=True)
        self._thread.start()
        return self

    def close(self, timeout=5.0):
        self._closing.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout)
        self._invalidate()
        return self

    # -- producing --------------------------------------------------------- #
    def publish(self, envelope) -> bool:
        """Queue an envelope for delivery.  Never raises, never blocks."""
        if self._closing.is_set():
            return False
        try:
            self._queue.put_nowait(envelope)
            return True
        except queue.Full:
            self.dropped += 1
            if self.dropped in (1, 10, 100) or self.dropped % 1000 == 0:
                log.warning("Event queue full: dropped %d event(s) so far "
                            "(broker unreachable?)", self.dropped)
            return False

    # -- connection -------------------------------------------------------- #
    def _connect(self):
        import pika  # imported late: the app runs without the package

        parameters = pika.URLParameters(self.url)
        parameters.connection_attempts = 1
        parameters.socket_timeout = self._connect_timeout
        parameters.blocked_connection_timeout = self._connect_timeout
        parameters.heartbeat = 30
        parameters.client_properties = {
            "connection_name": "%s/%s" % (self.service, uuid.uuid4().hex[:8]),
        }
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        channel.exchange_declare(exchange=self.exchange, exchange_type="topic",
                                 durable=True)
        # Confirms: basic_publish returns only once the broker has the message.
        channel.confirm_delivery()
        self._connection, self._channel = connection, channel
        self.connected = True
        self.last_error = None
        log.info("Event publisher connected to %s (exchange=%s)",
                 mask_url(self.url), self.exchange)
        return channel

    def _invalidate(self):
        self.connected = False
        for resource in (self._channel, self._connection):
            try:
                if resource is not None and resource.is_open:
                    resource.close()
            except Exception:  # closing a broken socket must not raise
                pass
        self._channel = None
        self._connection = None

    def _deliver(self, envelope):
        import pika

        body = json.dumps(envelope, ensure_ascii=False, default=str).encode("utf-8")
        properties = pika.BasicProperties(
            content_type="application/json",
            content_encoding="utf-8",
            delivery_mode=2,  # persistent
            message_id=envelope.get("event_id"),
            type=envelope.get("type"),
            app_id=self.service,
            timestamp=int(time.time()),
        )
        channel = self._channel if self._channel is not None and self._channel.is_open else self._connect()
        channel.basic_publish(exchange=self.exchange,
                              routing_key=envelope.get("type") or "event",
                              body=body, properties=properties)

    def _run(self):
        # pika logs every handshake at INFO; the bus reports its own state.
        logging.getLogger("pika").setLevel(logging.WARNING)
        # Warm the connection so the first event does not pay for the handshake
        # and /readyz can report the broker state from the very first probe.
        # A broker that is still starting is retried on demand below.
        try:
            self._connect()
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            log.info("Event broker not reachable yet (%s); will retry on the "
                     "first event", self.last_error)

        while not self._closing.is_set():
            try:
                envelope = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if envelope is None:
                break
            self._deliver_with_retries(envelope)
        self.connected = False
        self._invalidate()

    def _deliver_with_retries(self, envelope):
        for attempt in range(1, self._max_attempts + 1):
            try:
                self._deliver(envelope)
                self.published += 1
                return
            except Exception as exc:
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                self.connected = False
                self._invalidate()
                if attempt < self._max_attempts and not self._closing.is_set():
                    self.reconnects += 1
                    time.sleep(self._retry_delay)
        self.failed += 1
        log.error("Dropped %s event %s after %d attempts: %s",
                  envelope.get("type"), envelope.get("event_id"),
                  self._max_attempts, self.last_error)

    # -- introspection ----------------------------------------------------- #
    def status(self):
        return {
            "enabled": True,
            "exchange": self.exchange,
            "broker": mask_url(self.url),
            "connected": self.connected,
            "queued": self._queue.qsize(),
            "published": self.published,
            "failed": self.failed,
            "dropped": self.dropped,
            "reconnects": self.reconnects,
            "last_error": self.last_error,
        }
