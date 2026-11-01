# Playwright end-to-end tests

Browser tests for the whole workflow: accounts, projects, the land map, then
every tab of the planner (Water Resource, Geometry, Pipes, Trees, Components,
Map Overview, Validation, Report).

Everything runs against **the application's own MongoDB database**
(`MONGO_URI` from `config.py`, `FIS` by default) and its own uploads folder, so
every account, project and file a run creates can be opened and inspected in
the installed app. MongoDB must be running; nothing else has to be started.

The suite never drops the database. Before seeding it deletes only what it
owns: the accounts named `pw_*`, the projects they own and the files those
uploaded. Real accounts and projects are left untouched, and their presence
does not break any assertion (the tests only look for their own fixtures).

## Install

```bash
venv/Scripts/python.exe -m pip install -r tests/requirements-test.txt
python -m playwright install chromium   # optional: tests use the installed Chrome
```

The suite drives the system Chrome (`channel="chrome"`); when Chrome is not
installed Playwright falls back to its bundled Chromium.

## Run

```bash
python tests/run_tests.py                 # everything
python tests/run_tests.py -k water        # a subset (normal pytest options)
python tests/run_tests.py -x --lf         # stop at the first failure, rerun failed
```

The runner starts the Flask app on the first free port (7099 by default), seeds
the dataset, runs the tests in file order and shuts the server down.

## Result report

Every run writes to `tests/report/`:

| Path | Content |
| --- | --- |
| `index.html` | pytest-html report: each test, its duration, its logs, the screenshots |
| `screenshots/` | one screenshot per named step (`.jpg`), plus a `*-failure.jpg` for a failing test |
| `server.log` | the Flask request log of the run |
| `dataset.json` | the accounts and projects that were created |

Open `tests/report/index.html` in a browser after a run.

## Dataset (created once per run, in the app database)

* 1 Admin account — `pw_admin` / `AdminPass#2026` (bootstrapped in MongoDB,
  because the UI cannot create the very first admin).
* 3 Project Admins ("local admins"), each with a company agreement:
  `pw_localadmin_1..3` / `LocalAdmin#2026`.
* 3 Project Owners per Project Admin — 9 customers, `pw_owner_<admin>_<n>` /
  `Customer#2026`.
* 1 project per owner — 9 projects, each linked to its owner's Project Admin,
  with a ~160 m × 160 m land boundary and one water source.

All of it is visible in the app: sign in with `pw_admin` on the installed
application and the 9 projects, the 3 local admins (with their company
agreement) and the 9 customers are there. The KML maps, agreement PDFs and
photos land in the normal `uploads/` folder for the same reason.

Re-running the suite purges those fixtures first, so the dataset is rebuilt
from scratch instead of piling up duplicates. `python tests/seed.py --purge`
does that cleanup on its own when you want the database tidy without a run.

## Tests

| File | Covers |
| --- | --- |
| `test_1_land_maps.py` | uploads one `maps/*elevation*.kml` per project through the Project page; checks the filename, the element count and the reload |
| `test_2_water_resource.py` | Well / Basin / Other sub-tabs for all 9 projects; adds a well (depth, diameter) and a basin (L × W × D, area, m³) |
| `test_3_geometry.py` | Sectors / Zones for all 9 projects; Auto Generate sectors, validate, Auto Generate zones, validate |
| `test_4_pipes.py` | MP / SP / ZP pages for all 9 projects; Auto Generate on each; DripLine stays disabled until the rows are validated |
| `test_5_trees.py` | Rows / Trees for all 9 projects; generate + validate rows, generate + validate trees, DripLine unlocks |
| `test_6_components.py` | Valves and the other five component tabs for all 9 projects; Generate on each tab |
| `test_7_map_overview.py` | Map Overview for all 9 projects; Leaflet map, controls and sector widget buttons |
| `test_8_validation.py` | Validation for all 9 projects; runs the engineering checks and reads the result table |
| `test_9_report.py` | Report → DPA sub-tab for all 9 projects: the 22 chapters listed, the generate button following through to the printable report; header and legal footer on every sheet; report settings saved on `En-tête & documents` then printed |
| `test_10_roles.py` | role isolation: an owner sees only their project (list, every project page, map/API endpoints, writes) and is sent back with "Project not found" for the others; a local admin sees all 9 and may not open Users; the Admin sees everything |

The numbered files build on each other: project 1 is carried through the whole
pipeline (map → sectors → zones → pipes → rows → trees → components →
validation → report), so run them in order (the default).

`test_10_roles.py` is independent: it only needs the seeded accounts, so it
runs first (file names sort `test_10` before `test_1`) and never touches the
project data of the pipeline.

## Maps

Test 1 picks every KML in `maps/` whose file name contains `elevation`, in
natural order, and gives one to each project. With fewer than 9 such files the
list is reused in order (the run notes it); with 9 or more, every project gets a
different map.

## Notes

* Bootstrap, Leaflet and the icons come from CDNs. The tests open the modals and
  maps for real; when the CDN is unreachable the modal helpers reveal the form
  directly and the overview map check is skipped with a clear reason.
* No e-mail or WhatsApp message is ever sent: `SMTP_HOST` and
  `BLUETICKS_API_KEY` are blanked for the test server, so the one-time password
  code path only writes to the log.
* Because the database is shared, a run rewrites the `pw_*` fixtures: do not
  edit those 13 accounts or their 9 projects in the app, they are recreated on
  the next run and your edits are lost. Everything else is safe.
