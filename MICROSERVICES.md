# FIS microservices architecture

Status: **phase 0 (containers) and phase 1 (event messaging + audit service) are in place**.
The application still runs as one Flask monolith; it now ships as a container
image, publishes domain events to RabbitMQ, and one consumer already runs as a
separate process. This document is the map for the extraction that follows.

---

## 1. Where we start (measured)

| Module | Files | Lines | Responsibility |
|---|---:|---:|---|
| `irrigation/engineering` | 10 | 6 663 | hydraulics, piping, validation, regeneration, elevation, projection |
| `irrigation/components` | 4 | 1 935 | valves, connectors, reductions, terminators |
| `irrigation/geometry` | 2 | 1 407 | sectors, zones, map data |
| `irrigation/project` | 2 | 1 164 | projects, specs, water sources, KML import, documents |
| `irrigation/report` | 3 | 1 130 | reports, DPA technical report |
| `irrigation/hydrology` | 2 | 842 | main/second/zone pipes, driplines |
| `irrigation/trees` | 2 | 804 | rows and trees |
| `irrigation/water` | 2 | 751 | wells, basins, other sources |
| `irrigation/auth` | 3 | 604 | login, users, password flows |
| `irrigation/api` | 2 | 480 | GeoJSON API for the map |
| `irrigation/overview` | 2 | 416 | read-only map overview |
| `irrigation/estimation` | 2 | 371 | cost estimate and ROI |

Two structural facts dominate every design decision:

1. **One document per project.** `sector → zone → row → tree`, `network.pipes`,
   `network.components`, `reports`, `elevation_models` all live inside
   `project` (`irrigation/storage.py`). Every write is applied to an
   in-request copy and flushed as one `$set` at teardown.
2. **One writer per project.** That flush rewrites the whole sub-tree, so two
   services writing different parts of the same project concurrently would
   overwrite each other. **Splitting writers is therefore a data-model change,
   not a deployment change** — see phase 3.

---

## 2. Runtime topology (today)

```
                    ┌────────────────────────────┐
   browser ────────►│ web  (gunicorn, 4×4)       │
                    │ Flask monolith, :8000      │
                    └───────┬───────────┬────────┘
                            │           │ emits events (background thread)
                            │           ▼
                            │   ┌────────────────┐        ┌──────────────────┐
                            │   │ rabbitmq       │───────►│ worker           │
                            │   │ fis.events     │        │ fis.audit        │
                            │   │ (topic, dlq)   │        │ (python -m …)    │
                            │   └────────────────┘        └────────┬─────────┘
                            ▼                                      │
                    ┌────────────────┐                             │
                    │ mongo:7  FIS   │◄────────────────────────────┘
                    │ user/project   │      event_log (append-only)
                    │ + event_log    │
                    └────────────────┘
```

Brought up by `docker-compose.yml` (`mongo`, `rabbitmq`, `web`, `worker`).
Both app processes use the same image; only the command differs.

| Container | Command | Ports | Health probe |
|---|---|---|---|
| `web` | `gunicorn --config docker/gunicorn.conf.py wsgi:app` | 8000 | `GET /readyz` |
| `worker` | `python -m irrigation.events.worker` | – | `python -m irrigation.events.worker --check` |
| `mongo` | `mongod` | – | `mongosh ping` |
| `rabbitmq` | broker + management UI | 5672, 15672 | `rabbitmq-diagnostics ping` |

```bash
cp .env.example .env          # set SECRET_KEY (mandatory with >1 worker)
docker compose up -d --build
curl -s localhost:8000/readyz | jq
```

Without Docker the app is unchanged: `python app.py`, no broker needed
(`EVENTS_ENABLED=0` or simply no RabbitMQ — the publisher stays disconnected and
counts dropped events instead of slowing requests).

Ports 5672/15672 are published for host tooling; if something else already owns
them, set `RABBITMQ_PORT` / `RABBITMQ_MANAGEMENT_PORT` in `.env` (this machine
uses 5673/15673 because another project holds the defaults).

### Moving an existing database into the stack

The compose volume starts empty. To carry an existing local `FIS` database over:

```bash
mongodump --uri "mongodb://localhost:27017/FIS" --out ./dump
 docker compose cp ./dump mongo:/tmp/dump
docker compose exec mongo mongorestore --uri "mongodb://localhost:27017" /tmp/dump
```

---

## 3. Event contract

Envelope (`irrigation/events/bus.py`):

```json
{
  "event_id": "9f1c…",                 // idempotency key, unique per event
  "type": "piping.generated",          // also the routing key
  "version": 1,
  "occurred_at": "2026-11-03T14:30:02.218+00:00",
  "source": "fis-web",
  "project_id": 1,
  "actor_id": "1",
  "correlation_id": null,
  "payload": {"scope": "full", "counts": {"main_pipe": 6}}
}
```

Rules:

* **Events describe the past.** `sector.created`, never `create_sector`.
* **Published after commit.** `emit()` defers to the request; the teardown
  handler that flushes pending events is registered *before* `install_storage`,
  so it runs *after* the embedded-project flush. A failed write publishes
  nothing.
* **At-least-once.** Publisher confirms + persistent messages; consumers
  de-duplicate on `event_id` (the audit collection has a unique index).
* **Never blocking.** Bounded in-memory queue (`EVENTS_QUEUE_MAX`), background
  thread, lazy connect, 3 delivery attempts, then a counted drop.
* **Poison messages** go to `fis.dead` (`fis.audit.dlq`) and are acknowledged,
  so one bad event cannot stall a queue.
* **Versioning.** Additive payload changes keep `version`; a breaking change
  bumps it and consumers filter on `version`.

### Catalogue (`irrigation/events/catalog.py`)

| Event | Emitted from | Key payload |
|---|---|---|
| `project.created` | `project.create` | name, status, admin/owner ids |
| `project.updated` | `project.settings` | changed field names, water source count |
| `project.deleted` | `project.delete` | name, sector count |
| `sector.created` | `geometry.sector_add` | sector_id, name, orientation |
| `sectors.removed` | `geometry.sector_remove` | sector_ids, zone_ids, removed |
| `zone.created` | `geometry.zone_add` | zone_id, sector_id, name |
| `piping.generated` | `hydrology.piping_generate` | scope, counts, options |
| `validation.completed` | `engineering.run_validation` | status, passes, warnings, failures |

Consumers:

| Queue | Service | Handlers |
|---|---|---|
| `fis.audit` (binds `#`) | audit service (`worker`) | `audit()` → `event_log` |
| `fis.audit.dlq` | – | failed messages, inspected manually |

`GET /ops/events?limit=25&project_id=1` (admin only) returns the audited stream,
which is how the pipeline is verified end to end.

---

## 4. Target service map

| # | Service | Owns (data) | Sync API | Publishes | Consumes |
|---|---|---|---|---|---|
| 1 | **gateway / BFF** | – | `/`, templates | – | – |
| 2 | **identity** | `user` | `/auth/*`, `/users/*` | `user.created`, `password.reset` | – |
| 3 | **projects** | `project` (meta, spec, water, land) | `/api/projects` | `project.*` | `piping.generated` (status) |
| 4 | **geometry** | `project.geometry` (own doc) | `/api/projects/{id}/geometry` | `sector.*`, `zone.*` | `project.deleted` |
| 5 | **network** (piping + components) | `project.network` | `/api/projects/{id}/network` | `piping.generated`, `component.*` | `sector.*`, `zone.*` |
| 6 | **engineering** (CPU) | `project.reports` | `/api/projects/{id}/validation` | `validation.completed` | `piping.generated` |
| 7 | **estimation & report** (read models) | `estimation`, `report` docs | `/api/projects/{id}/estimate` | `report.rendered` | everything above |
| 8 | **audit / notifications** | `event_log` | `/ops/events` | – | `#` |

Extraction order is deliberate: services 7 and 8 are **read-only projections**
(no write conflicts, immediately scalable), 6 is **CPU-bound** (biggest latency
win), 4 and 5 need the data-model split, 2 last (it changes every login path).

---

## 5. Phase plan

- [x] **Phase 0 — containerise.** Image, compose stack, gunicorn, probes,
      env-driven config, static caching, Mongo pool/timeouts.
- [x] **Phase 1 — messaging + first separate process.** RabbitMQ topic
      exchange, envelope, producer wiring on 8 write paths, audit consumer,
      dead-letter queue, `/readyz`/`/ops/events`.
- [ ] **Phase 2 — async commands.** Move `regenerate_sectors`,
      `regenerate_zones`, `piping.generated`, elevation fetch and DPA report
      rendering off the request thread: POST enqueues a command, the UI polls a
      job status (`fis.commands` queue, `job` collection).
- [ ] **Phase 3 — data split.** Give geometry and network their own documents
      (`project_geometry`, `project_network`) so their writers no longer contend
      for one `project` document; migrate with a one-shot script and keep the
      embedded shape only as an export view.
- [ ] **Phase 4 — extract read services.** Estimation/report and audit as
      independently deployable services consuming `#`.
- [ ] **Phase 5 — identity + gateway.** Sessions in a shared store (Redis),
      `identity` service, gateway routing, per-service CI images and metrics.

---

## 6. Review of the current services (findings)

Applied in this change:

| Finding | Impact | Action |
|---|---|---|
| Flask dev server was the only runtime | 1 request at a time, `debug=True` | `gunicorn` `gthread`, 4 workers × 4 threads, `wsgi.py` |
| `SECRET_KEY = os.urandom(32)` when unset | sessions break across workers | documented as required; compose passes a fixed key |
| Mongo client with default 30 s selection timeout | a down database hangs requests and startup | `serverSelectionTimeoutMS=MONGO_TIMEOUT_MS` (5 s), `maxPoolSize` env |
| Static assets revalidated on every page | ~20 extra requests per navigation | `Cache-Control: public, max-age=STATIC_CACHE_SECONDS` |
| No health endpoint | orchestrator cannot restart a sick container | `/healthz` (liveness), `/readyz` (Mongo gate, broker reported) |
| Blocking work inside requests with no visibility | slow requests are invisible | event stream records `piping.generated` / `validation.completed` with counts |
| Session cookie defaults | XSS/CSRF exposure | `HttpOnly`, `SameSite=Lax`, `Secure` toggle |

Still open (need a decision before phase 3):

1. **Whole-project flush per request** (`storage.flush`) rewrites the project's
   entire `sectors`/`network` sub-tree. For Dhar1 (24 zones, 325 rows,
   5 715 trees) that is a multi-hundred-KB write per edit. Split into
   sub-document updates or per-aggregate documents — this is the single largest
   scaling limit and the main blocker for multiple writers.
2. **CPU in the request path.** Sector/zone regeneration, piping generation and
   elevation fetch hold a gunicorn thread for seconds; under 4×4 concurrency a
   few users can exhaust the pool. Phase 2 moves them to workers.
3. **No replica set.** Multi-document transactions (needed once services own
   separate documents) require MongoDB as a replica set.
4. **Read models rebuilt per request.** Estimation and report pages recompute
   from the project each time; they are natural projections maintained from
   events.
5. **No metrics/tracing.** `/ops/events` plus the publisher counters is a start;
   Prometheus metrics and correlation IDs across services come with phase 4.

---

## 7. Operating the stack

```bash
docker compose ps                      # health of all four containers
docker compose logs -f web worker      # app + consumer logs
docker compose exec worker python -m irrigation.events.worker --check
docker compose exec mongo mongosh FIS --eval 'db.event_log.countDocuments()'
docker compose exec mongo mongosh FIS --eval 'db.event_log.find().sort({occurred_at:-1}).limit(5).toArray()'
```

RabbitMQ management: <http://localhost:15672> (user/password from `.env`,
default `fis`/`fis`). Queues `fis.audit` and `fis.audit.dlq` show delivery and
failure rates.

Scaling: `docker compose up -d --scale worker=3` is safe — each consumer gets
its own connection and the audit handler is idempotent. Scaling `web` requires
`SECRET_KEY` to stay fixed (already enforced by compose) and a shared
`uploads` volume (already mounted).
