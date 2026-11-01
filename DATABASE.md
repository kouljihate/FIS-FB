# FIS database model (v0.4.0)

The app keeps its data in **two MongoDB collections** in the `FIS` database,
plus one derived collection that mirrors the user interface:

| collection | purpose |
|---|---|
| `user` | accounts, profile and role |
| `project` | land, water, pipes, components, geometry hierarchy and reports |
| `ui_registry` | every page and modal form of the UI, rebuilt from the code at start-up (see below) |

Default connection: `mongodb://localhost:27017/FIS` (override with `MONGO_URI`).

## One database, everywhere

The application, its scripts and the Playwright suite all use this single
database — the name in `config.Config.MONGO_URI`, `FIS` by default. There is no
test database: `tests/` reads and writes the same `user` and `project`
collections, so every account and project a test run creates is visible in the
app. A run first deletes only its own fixtures (the `pw_*` accounts, the
projects they own and their uploaded files) and never drops the database.

Because of that, nothing in the project may open a second database: a new
script must import `config.Config` (or reuse `tests/seed.DB_NAME`) instead of
naming a database of its own.

The collections, JSON-schema validators and indexes are created automatically
on application start (`irrigation/schema.py`). To create them explicitly:

```powershell
.\venv\Scripts\python.exe -m irrigation.schema
```

## `user`

| field | type | notes |
|---|---|---|
| `_id` | ObjectId | MongoDB id |
| `id` | int | application id, unique |
| `full_name` | string | display name |
| `username` | string | login, unique |
| `email` | string | login alternative, unique, lower-case |
| `phone` | string | |
| `password_hash` | string | Werkzeug hash |
| `profile` | enum | `Admin`, `Project Admin`, `Project Owner` |
| `cin` | string | identity card number |
| `photo` | string / null | relative path under `uploads/` |
| `is_active` | bool | disabled accounts cannot log in |
| `created_at` | date | |

The **first registered account becomes `Admin`**; later registrations are
`Project Owner`. An Admin can change roles from **Users** in the account menu.

### Role rules

| role | projects | user administration |
|---|---|---|
| `Admin` | all, full control | yes |
| `Project Admin` | all, full control | no |
| `Project Owner` | only projects they own | no |

Access is enforced in `irrigation/access.py`, which every project route, the
JSON API and the export/download routes use.  A Project Owner sees the
projects where `owner_user_id` is their id (linked on the create form or in the
project settings) plus the ones they created themselves; projects that predate
the link are matched to their account by `spec.owner.email` / name when the app
starts (`irrigation/schema.py::link_owner_accounts`).

## `project`

```
project
├── id, user_id, admin_user_id, owner_user_id, name, location, description,
│   status, created_at
│   (admin_user_id -> user.id of the linked Project Admin;
│    owner_user_id -> user.id of the linked Project Owner,
│    the account that may open the project)
├── land
│   ├── map            GeoJSON polygon (Land MAP)
│   ├── kml_file       imported KML/KMZ file name
│   ├── kml_features   parsed KML features
│   └── documents      land/owner/other document rows
├── water              [ {type: well|basin|reservoir|pump|canal|other,
│                         name, lat, lng, flow_lpm, notes} ]
├── sectors            [ Sector ]
│   └── zones          [ Zone ]
│       └── rows       [ Row ]
│           └── trees  [ Tree ]
├── network
│   ├── pipes          [ main_pipe | second_pipe | sub_pipe | dripline ]
│   └── components
│       ├── valves             [ master_valve | sector_valve | zone_valve | check_valve ]
│       ├── connectors         [ connector ]
│       ├── reductions         [ reduction ]
│       ├── air_removal        [ air_release ]
│       ├── pressure_reduction [ pressure_reduction ]
│       └── terminators        [ terminator ]
├── reports
│   ├── piping         last piping/engineering snapshot
│   └── validations    [ validation result ]
├── elevation_models   [ terrain model ]
└── spec               project settings (owner, config, rules, tree plan,
                       estimation (prices, items, discount, tax, roi),
                       land_documents, water_sources)
```

Child documents keep the fields the existing UI uses (`id`, `project_id`,
`name_en`, `name_ar`, `polygon`/`geometry`, `properties`, `validated`, ...).
They also carry an `_id` (ObjectId) so the templates can keep rendering rows.

### Indexes

| collection | indexes |
|---|---|
| `user` | `id` unique, `username` unique, `email` unique |
| `project` | `id` unique, `user_id`, `owner_user_id` (sparse) |
| `ui_registry` | `key` unique, `kind` + `id` |

## `ui_registry` (derived, never edited by hand)

`irrigation/ui_registry.py` rediscovers the interface on **every application
start** and reconciles this collection, so it always matches the code:

- a **page** added to the code (a GET view that renders a template) appears;
- a page removed from the code disappears;
- a **modal form** added to a template appears, one removed one disappears;
- the smart id of an entry that survives never changes.

| field | type | notes |
|---|---|---|
| `key` | string | unique key: the endpoint (`auth.login`, `error:404`) or `template#modal` |
| `kind` | enum | `page` or `modal` |
| `id` | string | smart id: `section.page` for a page, `section.page.modal` for a modal (Login is `0.0`) |
| `section` | int | first part of `id`; one section per workflow tab |
| `name`, `description` | string | from the curated catalog when it knows the entry, generated otherwise |
| `url` | string / null | route rule of the page; for a modal the URL of the page that shows it |
| `endpoint` | string / null | pages: the Flask endpoint |
| `blueprint` | string / null | pages: the blueprint the endpoint belongs to |
| `templates` | array | pages: the templates the view renders |
| `template`, `modal`, `raw_id` | string | modals: declaring template, stable id, id with its Jinja expression |
| `pages`, `primary_page` | array / string | every page rendering the modal, and the page it is numbered under |
| `updated_at` | date | last sync |

Modals rendered once per row (`editModal{{ s.id }}`) are stored once, with the
Jinja expression stripped from `modal`.

The curated catalog - smart ids, names and descriptions - lives in
[`irrigation/ui_registry.json`](irrigation/ui_registry.json).  An entry the
catalog does not know is still stored, with an id allocated next to its
section/page, so no manual step is needed when the interface grows.  Rebuild the
collection on demand with:

```powershell
.\venv\Scripts\python.exe -m irrigation.ui_registry
```

### How the app talks to the embedded model

`irrigation/storage.py` wraps `mongo.db`. Queries such as
`mongo.db.trees.find({'project_id': 1})`, `insert_one`, `update_one`,
`update_many`, `delete_many` and `count_documents` keep working, but they read
and write the nested arrays inside the project document.

- Reads return copies; writes are applied to an in-request copy of the project
  and flushed once at request teardown (large generations stay fast).
- `get_next_id()` keeps its counters in memory, seeded from the highest stored
  id; there is no third `counters` collection.
- Raw elevation samples are cached in process memory; the per-project terrain
  model is persisted in `elevation_models`.

### Limits

- A MongoDB document is capped at 16 MB. The current test project stores 5,000
  trees in ~1.6 MB; very large projects (tens of thousands of trees) should be
  split or moved back to child collections.
- The per-request write cache assumes a single application process/host, which
  matches the local deployment. Running several workers against the same
  project concurrently can lose updates.
