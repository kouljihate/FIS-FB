# FIS database model (v0.4.0)

The app now uses exactly **two MongoDB collections** in the `FIS` database:

| collection | purpose |
|---|---|
| `user` | accounts, profile and role |
| `project` | land, water, pipes, components, geometry hierarchy and reports |

Default connection: `mongodb://localhost:27017/FIS` (override with `MONGO_URI`).

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
JSON API and the export/download routes use.

## `project`

```
project
├── id, user_id, admin_user_id, name, location, description, status, created_at
│   (admin_user_id -> user.id of the linked Project Admin)
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
| `project` | `id` unique, `user_id` |

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
