"""Consumer worker for the FIS event bus (the audit service).

Run it as its own container/process:

    python -m irrigation.events.worker                 # consume fis.audit
    python -m irrigation.events.worker --check         # connect, report and exit
    python -m irrigation.events.worker --queue fis.audit --prefetch 50

Delivery contract: at-least-once.  Handlers are idempotent (the audit handler
de-duplicates on ``event_id``) and a message that a handler rejects is copied
to the dead-letter queue and acknowledged, so one poison message cannot block
the pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time

log = logging.getLogger("fis.events.worker")


def _configure_logging(level="INFO"):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # The consumer logs connect/retry itself; pika's INFO handshake noise hides it.
    logging.getLogger("pika").setLevel(logging.WARNING)


def _connection_parameters(url, timeout=5.0, service="fis-audit-worker"):
    import pika

    parameters = pika.URLParameters(url)
    parameters.connection_attempts = 1
    parameters.socket_timeout = timeout
    parameters.heartbeat = 30
    parameters.client_properties = {"connection_name": service}
    return parameters


class Consumer:
    """Blocking consumer that survives broker restarts."""

    def __init__(self, url, exchange, queue, handlers, *, dead_exchange="fis.dead",
                 prefetch=20, service="fis-audit-worker", db=None,
                 retry_delay=5.0):
        self.url = url
        self.exchange = exchange
        self.dead_exchange = dead_exchange
        self.queue = queue
        self.handlers = tuple(handlers)
        self.prefetch = prefetch
        self.service = service
        self.db = db
        self.retry_delay = retry_delay
        self._stopping = False
        self._connection = None
        self.processed = 0
        self.dead_lettered = 0

    # -- topology ---------------------------------------------------------- #
    def _declare(self, channel):
        channel.exchange_declare(exchange=self.exchange, exchange_type="topic",
                                 durable=True)
        channel.exchange_declare(exchange=self.dead_exchange,
                                 exchange_type="topic", durable=True)
        channel.queue_declare(queue=self.queue, durable=True)
        channel.queue_bind(queue=self.queue, exchange=self.exchange,
                           routing_key="#")
        channel.queue_declare(queue=self.queue + ".dlq", durable=True)
        channel.queue_bind(queue=self.queue + ".dlq", exchange=self.dead_exchange,
                           routing_key="#")

    def connect(self, timeout=5.0):
        import pika

        self._connection = pika.BlockingConnection(
            _connection_parameters(self.url, timeout, self.service))
        channel = self._connection.channel()
        self._declare(channel)
        return self._connection

    # -- consuming --------------------------------------------------------- #
    def run(self):
        import pika

        delay = self.retry_delay
        while not self._stopping:
            try:
                self.connect()
                self._connection.channel().queue_declare(queue=self.queue,
                                                         durable=True, passive=True)
                channel = self._connection.channel()
                channel.basic_qos(prefetch_count=self.prefetch)
                channel.basic_consume(queue=self.queue,
                                      on_message_callback=self._on_message)
                log.info("consuming %s from %s (prefetch=%d, handlers=%s)",
                         self.queue, self.exchange, self.prefetch,
                         ", ".join(getattr(h, "__name__", repr(h))
                                   for h in self.handlers))
                delay = self.retry_delay  # a successful connect resets backoff
                channel.start_consuming()
            except pika.exceptions.AMQPConnectionError as exc:
                if self._stopping:
                    break
                log.warning("broker unreachable (%s); retrying in %.0fs", exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
            except KeyboardInterrupt:
                break
            except Exception as exc:  # connection reset, channel closed, ...
                if self._stopping:
                    break
                log.warning("consumer connection lost (%s: %s); retrying in %.0fs",
                            type(exc).__name__, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
            finally:
                self._close()
        log.info("worker stopped (processed=%d, dead-lettered=%d)",
                 self.processed, self.dead_lettered)

    def _on_message(self, channel, method, properties, body):
        try:
            envelope = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.error("unparseable message (%d bytes) -> dead letter", len(body))
            self._dead_letter(channel, method, properties, body, envelope=None)
            return

        for handler in self.handlers:
            try:
                handler(self.db, envelope)
            except Exception:
                log.exception("handler %s failed on %s (event %s)",
                              getattr(handler, "__name__", handler),
                              envelope.get("type"), envelope.get("event_id"))
                self._dead_letter(channel, method, properties, body, envelope)
                return

        channel.basic_ack(delivery_tag=method.delivery_tag)
        self.processed += 1

    def _dead_letter(self, channel, method, properties, body, envelope):
        import pika

        routing_key = (envelope or {}).get("type") or "unparseable"
        try:
            channel.basic_publish(
                exchange=self.dead_exchange, routing_key=routing_key, body=body,
                properties=pika.BasicProperties(
                    content_type="application/json", delivery_mode=2,
                    message_id=(envelope or {}).get("event_id"),
                    app_id=self.service),
            )
        except Exception:
            log.exception("could not dead-letter %s", routing_key)
        # Acknowledge either way: a poison message must not be redelivered
        # forever, and a dead-letter publish failure is logged above.
        channel.basic_ack(delivery_tag=method.delivery_tag)
        self.dead_lettered += 1

    # -- lifecycle --------------------------------------------------------- #
    def stop(self, *_args):
        self._stopping = True
        connection = self._connection
        if connection is not None and connection.is_open:
            try:
                # Safe to call from the signal handler: runs on the I/O thread.
                connection.add_callback_threadsafe(connection.close)
            except Exception:
                pass

    def _close(self):
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            if connection.is_open:
                connection.close()
        except Exception:
            pass


def _install_signal_handlers(consumer):
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, consumer.stop)
        except (ValueError, OSError):  # not the main thread / unsupported
            pass


def _build_db():
    """Open the real Mongo database through the app's own configuration."""
    from irrigation import create_app
    from irrigation.extensions import mongo
    from irrigation.schema import real_database
    from irrigation.events.handlers import ensure_event_indexes

    app = create_app()
    with app.app_context():
        db = real_database(mongo)
        ensure_event_indexes(db)
        config = {
            "url": app.config.get("RABBITMQ_URL"),
            "exchange": app.config.get("EVENTS_EXCHANGE") or "fis.events",
            "dead": app.config.get("EVENTS_DEAD_EXCHANGE") or "fis.dead",
            "queue": app.config.get("EVENTS_AUDIT_QUEUE") or "fis.audit",
            "service": app.config.get("SERVICE_NAME") or "fis-audit-worker",
        }
    return db, config


def parse_args(argv):
    parser = argparse.ArgumentParser(description="FIS event consumer (audit service).")
    parser.add_argument("--queue", help="queue to consume (default: fis.audit)")
    parser.add_argument("--routing-key", default="#", help="binding pattern (default #)")
    parser.add_argument("--prefetch", type=int, default=20)
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    parser.add_argument("--check", action="store_true",
                        help="connect, report queue depth and exit (health probe)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    _configure_logging(args.log_level)
    try:
        import pika  # noqa: F401  (fail fast with a clear message)
    except ImportError:
        log.error("pika is not installed: pip install -r requirements.txt")
        return 2

    from irrigation.events.handlers import HANDLERS

    db, config = _build_db()
    queue = args.queue or config["queue"]
    handlers = HANDLERS.get(queue)
    if not handlers:
        log.error("no handlers registered for queue %s", queue)
        return 2

    consumer = Consumer(config["url"], config["exchange"], queue, handlers,
                        dead_exchange=config["dead"], prefetch=args.prefetch,
                        service=config["service"], db=db)

    if args.check:
        try:
            connection = consumer.connect(timeout=5)
            channel = connection.channel()
            declared = channel.queue_declare(queue=queue, durable=True,
                                             passive=True)
            depth = declared.method.message_count
            connection.close()
        except Exception as exc:
            log.error("check failed: %s: %s", type(exc).__name__, exc)
            return 1
        log.info("ok: %s reachable, %d message(s) waiting", queue, depth)
        return 0

    _install_signal_handlers(consumer)
    consumer.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
