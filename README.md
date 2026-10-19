# FIS - Irrigation Planning System

Flask web application for planning and costing orchard / vineyard irrigation
systems. Field geometry, pipe hydraulics and agronomic water demand are computed
in one place, so the estimate and the report follow the design instead of being
maintained by hand.

Built from `OPENCODE.md` in the FreeBuff workspace.

## Navigation

The main tabs follow the required order:

```
Project - Water Resource - Geometry - Hydrology1 - Trees - Hydrology2 - Estimation - Report
```

| Tab | Sub-tabs |
| --- | --- |
| Project | project list, dashboard, settings, team |
| Water Resource | Well, Basin, Other |
| Geometry | Sectors, Zones |
| Hydrology1 | Main Pipe (MP), Secondary Pipe (SP), Zone Pipe (ZP) |
| Trees | Rows, Trees |
| Hydrology2 | Dripline, Reduction, Pressure Reduction, Air Eliminator, Connectors to Dripline |
| Estimation | Configuration, Result (Calculation) |
| Report | Charts, Bilan Hydrique, Technical Report (DPA model) |

## Data model

`lands > project > sectors > zones > rows > trees`, stored as flat MongoDB
documents in one collection per level, each carrying a `project_id`.
Deleting a sector cascades to its zones, rows and trees; deleting a project
removes every record beneath it, its `kml_files` metadata and its uploaded map.

**A project cannot be created without its land map.**  The upload (KML, KMZ or
DXF) is stored under `uploads/<project_id>/` and its largest closed outline
becomes the land boundary, so the design is always anchored to real land.  The
project document keeps a `map` summary (kind, original name, stored name, size,
uploaded_at) and a mirror record in the `kml_files` collection, which is what the
cascade above cleans up.  `utils/mapfiles.py` does the reading with the standard
library only: KML/KMZ are geographic lon/lat, a DXF drawing is projected metres.

The FreeBuff build uses **its own database** (`fis_freebuff`) so it can never
collide with the other copies of this application running against the same
MongoDB server.  Uploaded maps land in `uploads/<project_id>/`; the test suite
points `UPLOAD_FOLDER` at a temporary directory, so a test run never touches it.

## Setup

```bash
cd D:\Projects\FreeBuff\FIS
pip install -r requirements.txt
copy .env.example .env        # then edit SECRET_KEY
python app.py                 # http://127.0.0.1:5000
```

Requirements: Python 3.10+ and a running MongoDB. The first account you register
becomes a **Full Admin**; later self-registrations are **Customers** and only a
Full Admin may promote them. No default password is shipped in the source.

## Tests

```bash
python -m pytest
```

136 tests. The unit tests cover the geometry, hydraulic, emitter and agronomy
engines plus the KML/KMZ/DXF readers; the route tests drive the app through HTTP,
including project isolation between two accounts, the mandatory map upload, the
edit and remove flows and the bilingual chrome arrangement (Arabic above English
in tabs, buttons and table headers; English left, Arabic right in headings). Database tests use a separate `fis_freebuff_test` database
and skip cleanly when MongoDB is unreachable.

## Engineering basis

Pure engines live under `irrigation/utils/` and are independent of Flask:

**Geometry** (`geometry_calc.py`) - polygon area, perimeter and centroid, plus
real split / merge / swap. Boundaries are reprojected to the local UTM zone with
`pyproj` before any measurement, because a few percent of area error drives the
whole downstream estimate. Polygon algebra uses `shapely`.

> The coordinate system is **explicit**. Guessing it from the numbers is unsafe
> for derived polygons: a 100 m x 50 m strip in projected metres has every
> coordinate inside +/-180 / +/-90, so shape detection would read it as lon/lat
> and report an area about ten orders of magnitude too large. `split_into_n` and
> friends therefore take a `geographic` flag, resolved once from the project's
> own land boundary and threaded through every derived polygon.

**Equal-area partition** - sectors and zones are sliced by bisecting the cut
position until the piece has exactly the target area. Recursive halving would
give three zones of 50 % / 25 % / 25 %, which is not acceptable when the zone
area drives valve sizing.

**Hydraulics** (`hydraulics.py`) - Hazen-Williams (forward, inverse flow and
inverse diameter), Darcy-Weisbach with the Swamee-Jain friction factor, minor
losses, and the Christiansen multi-outlet reduction factor:

```
hf = 10.67 * L * Q^1.852 / (C^1.852 * D^4.871)          Hazen-Williams, SI
F  = 1/(m+1) + 1/(2N) + sqrt(m-1)/(6N^2)                Christiansen, m = 1.852
```

**Emitters** (`emitters.py`) - discharge law `q = k * p^x`, distribution
uniformity (lowest quarter over mean) and ASABE EP458 emission uniformity
`EU = 100 * (1 - 1.27 * CV / sqrt(n)) * (q_min / q_mean)`.

**Agronomy** (`agronomy.py`) - FAO-56 demand chain, USDA SCS effective rainfall,
total and readily available water, FAO-29 leaching requirement and the pump duty
point `P = rho * g * Q * H / eta`.

## API

| Endpoint | Returns |
| --- | --- |
| `GET /api/project/<id>/geometry` | sectors with boundaries, metrics and nested zones |
| `GET /api/project/<id>/summary` | record counts and aggregated areas |
| `GET /api/project/<id>/collection/<name>` | a child collection, optional `?kind=` |

Every endpoint is scoped by project and applies the same access check as the HTML
pages; unauthenticated API calls get `401`, not an HTML redirect.

## Layout

```
app.py, config.py            entry point and configuration
irrigation/
  __init__.py                application factory, error handlers, i18n context
  constants.py               collections, profiles, the mandated navigation
  models.py                  User + password hashing
  extensions.py              PyMongo, Flask-Login
  auth/                      sign in, sign up, profile, language
  project/                   project aggregate, ownership, statistics
  geometry/                  sectors and zones with split / merge / swap
  water_resource/            wells, basins, other sources
  hydrology/                 MP / SP / ZP with velocity and head loss
  trees/                     rows and trees, generated from the geometry
  hydrology2/                dripline, reductions, air eliminators, connectors
  estimation/                line items, discount, tax, totals
  report/                    charts, bilan hydrique, technical report
  api/                       read-only JSON
  utils/                     geometry, hydraulics, emitters, agronomy, CRUD, i18n
  templates/                 Jinja templates (Bootstrap markup)
tests/                       136 tests
static/                      the application's static root (see below)
```

## Front end

Bootstrap 5 with the **Bootswatch "Journal"** theme, vendored locally so the app
works offline. `static/` is the Flask static root:

```
static/
  css/     bootstrap.min.css, bootstrap-icons.min.css, app.css, app-overrides.css
  themes/  journal.min.css            <- the Bootswatch skin
  js/      bootstrap.bundle.min.js, app.js
  fonts/   Comfortaa (Latin), VIP-Rawy (Arabic), bootstrap-icons
```

Every page extends `templates/base.html`, which loads the stylesheets in order -
Bootstrap first, then the Journal theme, then icons, then `app.css` (fonts and
the bilingual helpers) and finally `app-overrides.css`. Loading Journal *after*
Bootstrap is what makes the theme win; `app.css` after it is what keeps the
Comfortaa / VIP-Rawy fonts and the Arabic right-to-left rules authoritative.

The chrome is **bilingual everywhere**: both languages are always on screen and
neither can be hidden. There is no language picker. Two arrangements are used,
both defined once in `templates/shared/_macros.html`:

| Arrangement | Where | Example |
| --- | --- | --- |
| Arabic on top, English below | tabs, buttons, table headers, stat tiles | `bilingual_stack`, `bilingual_button`, `th` / `bi_th` |
| English left, Arabic right | page titles, section headings, form labels | `bilingual_heading`, `bilingual_label` |

Arabic is set in VIP-Rawy and marked `lang="ar" dir="rtl"` on the element that
holds it, so it reads correctly even though the document stays left-to-right.
Labels that originate in the route modules (table columns such as
`"Diameter (mm)"` and field names such as `"diameter_mm"`) are translated by
`utils.i18n.label_ar`, exposed to Jinja as `tar`; anything unknown simply shows
in English, so a new column never renders blank.

`/auth/language` still exists and still flips the document to RTL when posted to
directly (the route tests exercise it), but no screen offers it any more.

One stylesheet is yours (`css/app.css`) and one is the project's
(`css/app-overrides.css`); the second holds only what the screens need on top of
Bootstrap - chart canvas sizing, the actions toolbar, print rules.

The whole interface is pastel. Six hues — blush, peach, butter, mint, sky and
lavender — each with an accent value and a light `-tint` surface, are declared
once in `:root` of `css/app.css`, and every surface is drawn from them: the
lavender-white page, the sky/lavender/blush navbar gradient, the sky workflow
tabs, the peach sub-tabs, the footer, table heads, buttons, badges, forms and
dialogs. Bootstrap's own tokens (`--bs-body-bg`, `--bs-primary`, `--bs-link-*`,
`--bs-card-*`) are re-pointed at the palette first, so components the
stylesheet does not name still follow it. Cards are read in three bands — the
header lavender, the body mint, the footer butter (`--card-header-pastel`,
`--card-body-pastel`, `--card-footer-pastel`) — and tables inside a card let the
body band show through. Two things are deliberately not pastel: the secondary
*text* colour stays a readable grey even though the secondary *chip* is
lavender, and the delete dialog keeps its rose/peach warning tones.

### Page structure

`base.html` provides the shell every screen reuses: a pastel planner navbar with
the brand on both sides, the eight main tabs as `project-workflow-nav` (each tab
a stacked English/Arabic `bilingual_button`), the active tab's sub-tabs below it,
a fixed footer with the version badge, flash messages as auto-dismissing toasts,
and the global loading overlay. The navbar is emitted from a `navbar` block, so
the signed-out screens can substitute their own chrome. Screens use the per-page header grids from
`app.css`: `projects-page-header`, `geometry-sectors-header`,
`geometry-zones-header` and the generic `wf-page-header`, each a 15 % / 70 % / 15 %
grid of English title, centre icon and Arabic title, collapsing to one column on
narrow screens.

Signed-out visitors get `auth/_header.html` in place of the navbar: a
three-column grid — `Irrigation Planning System`, the `FIS` chip in the middle,
`نظام تخطيط الري` — with the two account tabs (Sign in, Create account) beneath
it, the active one filled in lavender and marked `aria-current="page"`. Each
auth screen keeps its own card under the tabs, so the pair is the only way
between them; the old "No account yet?" / "Already registered?" links are gone.

The project screens are the entry point: `project/create.html` takes the
mandatory map upload, `project/list.html` renders one card per project with its
counts, its land badge and **Edit** / **Remove** actions, and
`project/edit.html` changes the identity, the linked map or the boundary.

Forms and CRUD use Bootstrap components throughout: `card`, `table table-hover`,
`btn-circle-sm` row actions, `form-control` / `form-select`, and
Bootstrap modals in place of the earlier `<dialog>` elements. `static/js/app.js`
adds only what data attributes cannot express: pre-filling a modal from the row
that opened it, the two-row merge/swap selection, the confirm-before-delete hook,
toasts, the loading overlay and the canvas charts (drawn in the theme's primary
colour, read from the `--bs-primary` custom property).

## Status

Implemented: authentication and profiles, project aggregate with a mandatory
KML/KMZ/DXF land map and land metrics, project edit and remove, geometry
(add / edit / rename / remove / split / merge / swap / generate), zone
subdivision, water sources, MP/SP/ZP with live velocity and friction loss,
row generation from sector geometry, tree placement along row centrelines,
dowstream components, estimation with totals chain, charts, bilan hydrique,
technical report, and the JSON API. Bilingual chrome (English / Arabic with RTL).

Not yet implemented: map rendering and map export, AI-assisted generation, the
module/phase file, PDF export, and full Arabic translation of every generated
table.
