# Changelog

## 2.6.0 - 2026-10-14

The signed-out screens get their own header: the application name split into
three columns with the short name in the middle, and the two account pages
offered as tabs instead of cross-links.

### Signed-out header
- `auth/_header.html` replaces the application navbar on **Sign in** and
  **Create account**.  It is a three-column grid -
  `Irrigation Planning System | FIS | نظام تخطيط الري` - with the short name in
  the middle column as a white chip, so it lands exactly on the centre line of
  the page at every width.
- Underneath sit the two account tabs - Sign in and Create account - each a
  stacked bilingual button; the active one is filled in lavender and marked
  `aria-current="page"`, so the pair works as the only navigation a signed-out
  visitor needs.
- The "No account yet?" and "Already registered?" paragraphs are gone; the tab
  strip is now the way between the two screens.

### Notes
- `base.html` gains a `navbar` block so a screen can substitute the chrome.
- `.auth-header`, `.auth-header-grid` and `.auth-tabs` live in the pastel theme
  block of `css/app.css` and are drawn from the same palette as every other
  surface; the signed-in navbar is untouched.

## 0.5.0 - 2026-10-13

The whole interface is pastel: soft tinted surfaces everywhere instead of one
accent colour on a white page, the way a Nicepage pastel template is built.

### Pastel palette
- Six hues - blush, peach, butter, mint, sky and lavender - each with an accent
  value and a light "-tint" surface, are declared once in `:root` of
  `static/css/app.css`.  Every surface below is drawn from them, so the whole
  application re-tints from one place.
- Bootstrap's own tokens (`--bs-body-bg`, `--bs-primary`, `--bs-link-*`,
  `--bs-card-*`, ...) are re-pointed at the palette first, so components this
  build never names still follow it.

### Where the tints land
- Body and page take a lavender-white; the navbar is a sky - lavender - blush
  gradient with dark ink in place of the old dark bar; the footer is lavender.
- Workflow tabs sit on a sky strip with the active tab in lavender, the sub-tabs
  on a peach strip, and table heads on the lavender tint.
- Cards keep their three bands (header lavender, body mint, footer butter) and
  data tables let the body band show through.
- Buttons, badges, forms and dialogs are pastel too: soft fills with dark ink,
  because white text on a pale colour is unreadable.

### Notes
- `--bs-secondary-rgb` stays a readable grey even though the secondary *chip* is
  lavender, because `.text-secondary` is used across the screens and takes its
  colour from that token.
- The delete-confirmation dialog and the flash toasts are pastel as well; their
  close crosses are dark ink rather than white.

## 0.4.0 - 2026-10-13

A project is now anchored to its land: the map is mandatory, projects can be
edited, and the list cards carry Edit and Remove.

### Land map required on creation
- A new project must be given its land as a **KML, KMZ or DXF** file; the form is
  a multipart upload and refuses to submit without it.
- The boundary is read from the **largest closed outline** in the file, so a
  survey export that also carries annotation outlines still yields the parcel.
- `utils/mapfiles.py` does the reading with the standard library only: KML is
  parsed as XML (KMZ is unzipped first), DXF group codes are walked to pick up
  `LWPOLYLINE` and the older `POLYLINE`/`VERTEX` form.  KML/KMZ are geographic
  lon/lat; a DXF drawing is projected metres.
- The upload is stored under `uploads/<project_id>/` with a generated name while
  the original name is kept for the download; the project document records a
  `map` summary and a mirror row in the `kml_files` collection.

### Edit and remove
- New **Edit project** page (`/projects/<id>/edit`): name, description, an
  optional replacement map (which redraws the boundary) and an optional typed
  boundary override.  Reached from the project toolbar and from every list card.
- **Remove** is on the list card too, behind the bilingual confirm, and only for
  a user who may actually delete the project.
- **Fixed:** removing a project left the project itself in the list.  The cascade
  deleted its children by `project_id` and then looked for the parent by the same
  key, but the project document is found by `_id`.
- **Fixed:** the remove route compared the project's `owner_id` (an ObjectId)
  with `current_user.id` (a string) and so refused every owner who was not a full
  admin, even though their card offered the button.  Both the route and the card
  now share `service.can_delete`.
- Deleting a project also removes its uploaded map from disk.

### Also
- The linked map is shown on the project overview and the edit page with a
  **Download** link; `/projects/<id>/map` serves the stored file.
- New labels (`edit_project`, `map_file`, `linked_map`, `replace_map`,
  `download`) follow the bilingual rules above.
- A 413 handler explains an upload past the 32 MiB limit instead of showing the
  default error page.
- Tests upload a generated KML instead of typing boundary coordinates, and point
  `UPLOAD_FOLDER` at a temporary directory so a run no longer writes into the
  application's `uploads/`.

### Card colour bands
- Every card separates its three zones by a pastel tint: the header is periwinkle,
  the body mint and the footer sand, so the parts of a card are told apart at a
  glance.  The three tones are CSS variables (`--card-header-pastel`,
  `--card-body-pastel`, `--card-footer-pastel`) set once in `static/css/app.css`.
- The body tint is applied to the card itself, so a card whose content is a bare
  `.table-responsive` (no `.card-body`) is tinted too.  The delete-confirmation
  modal keeps its own red header and pink body.

## 0.3.0 - 2026-10-13

Both languages always on screen, and the language picker removed.

### Bilingual everywhere
- Every label, heading, table header, button and tile now prints English *and*
  Arabic together; nothing is hidden behind a language switch.
- **Arabic on top, English below** in tabs, buttons, table headers and stat
tiles. **English on the left, Arabic on the right** in page titles, section
headings and form labels.
- Table headers became bilingual across every screen: geometry (sectors, zones),
  trees (rows, trees), the generic CRUD tables behind water resources,
  hydrology and hydrology 2, the estimation tables and all three report pages.
- Row labels in the bilan hydrique and technical report are bilingual too, as are
  the assumption fields.
- Route-supplied column labels (`"Diameter (mm)"`, `"Nominal flow (L/h)"`, …)
  and field names (`"static_level_m"`, …) are translated through a new
  `utils.i18n.label_ar`, exposed to Jinja as `tar`. Unknown labels fall back to
  English rather than rendering empty.
- New macros in `templates/shared/_macros.html`: `bilingual_stack`, `th`,
  `bi_th`, `bi_stack`, `bi_label`, `bi_button` (plus the existing side-by-side
  heading and label helpers), so no template invents its own markup.
- The last English-only prose went through the same helpers: report and page
  subtitles (charts, bilan hydrique, technical report), the estimation footer
  hint and item counter, empty states, the member count, the delete/ reset
  confirmations (including the ones raised by `data-confirm`), select options
  (irrigation system, kinds) and form-control placeholders / `aria-label`s.
- `app-overrides.css` styles the stacked pair; the Arabic face (VIP-Rawy) and  the right-to-left direction are applied to the Arabic line itself, so it reads
  correctly while the document stays left-to-right.

### Language picker removed
- The language `<select>` is gone from the navbar. `/auth/language` remains and
  still flips the document to RTL when called directly, which is what the route
  test exercises.

### Tests
- The bilan regression assertions now look across the bilingual markup between a
  row label and its value cell; the numbers they check are unchanged.
- Three new tests pin the rules themselves: the chrome is bilingual with no
  language picker, buttons/tabs/table headers print Arabic before English in the
  markup, and headings/labels print English before Arabic. 109 tests pass.

## 0.2.0 - 2026-10-13

Front end rebuilt on Bootstrap 5 with the Bootswatch "Journal" theme, applied to
every screen rather than only the chrome.

### Theme and assets
- Vendored **Bootstrap 5.3.3**, **Bootswatch Journal 5.3.3** and **Bootstrap Icons
  1.11.3** under `static/`, so the application is themed and icon-decorated
  without a network call.
- `static/` is now the Flask static root (previously `irrigation/static`), matching
  the layout the project uses: `css/`, `themes/`, `js/`, `fonts/`.
- The Comfortaa (Latin) and VIP-Rawy (Arabic) faces are declared with
  `@font-face` and made the default for the whole document, including the
  Bootstrap components.
- Stylesheets load in the order Bootstrap, Journal, icons, `app.css`,
  `app-overrides.css`, so the theme overrides Bootstrap and the project fonts and
  Arabic rules override the theme.

### Layout
- `base.html` rebuilt on Bootstrap: a planner navbar carrying the English brand
  on the left and the Arabic brand on the right, the collapse toggle for narrow
  screens, the language selector, the account menu and the fixed footer with the
  version badge.
- The eight main tabs and every sub-tab are rendered as Bootstrap nav-tabs in the
  existing `project-workflow-nav` / `project-workflow-subnav` shells, each tab a
  stacked English/Arabic bilingual button with its own icon. The required tab
  order is unchanged.
- Flash messages are Bootstrap toasts in the fixed container; a global loading
  overlay covers the long generation posts.

### Screens
- Every template converted to Bootstrap components - cards, `table table-hover`,
  `form-control` / `form-select` / `form-check`, `btn` variants, `text-bg-*`
  badges, `table-responsive` wrappers and `alert` banners - with the per-page
  bilingual header grids from `app.css` (`projects-page-header`,
  `geometry-sectors-header`, `geometry-zones-header`, `wf-page-header`).
- The `<dialog>` elements are gone: all add / edit / rename / generate / split /
  merge / swap / delete flows are Bootstrap modals, and the destructive delete
  uses the styled confirmation header, body and button.
- `static/js/app.js` rewritten against the Bootstrap bundle: it pre-fills a modal
  from the row that opened it, keeps the merge/swap two-row selection and its
  live list, confirms destructive submits, shows toasts, drives the loading
  overlay, and draws the canvas charts in the theme's primary colour read from
  `--bs-primary`.
- A shared macro library (`templates/shared/_macros.html`) holds the bilingual
  heading, label and button building blocks so no screen invents its own markup.

### Verified
- The 106 tests still pass under both the system interpreter and the project venv.
- Live over HTTP: all ten vendored assets return 200 with the expected content,
  every one of 25 crawled pages renders inside the Bootstrap shell, and a browser
  check confirms the computed styles - body font `Comfortaa, VIP-Rawy`, primary
  colour `rgb(235, 104, 100)` (Journal's `#eb6864`), the 15/70/15 header grid at
  desktop width, the modal backdrop and pre-fill, and a real flash toast.

## 0.1.1 - 2026-10-13

Corrections found by driving the running application rather than the test suite.

### Hydraulics and water balance
- **Peak design flow** is now the daily gross depth delivered inside the operating
  window, not the whole soil-reserve event. The event is spread over several days
  by the irrigation interval, so the earlier figure inflated the flow by roughly
  the interval length: a 5 ha block reported 149.8 m3/h where 17.68 m3/h is right.
- **Critical path friction**: pipes at one level are parallel routes, so the loss
  is the worst branch at each level with its own share of the flow, instead of
  every pipe summed as if in series at the full block flow. A single recorded
  zone pipe no longer appears to carry the whole block and report a 6 m/s
  velocity. Total dynamic head for the reference block fell from about 3 967 m to
  37.4 m, and the pump duty from an absurd figure to 2.77 kW.
- The flow divisor follows the hierarchy - a main carries the block, each
  secondary serves a sector, each zone pipe serves a zone - and the bilan shows
  the divisor used, with a badge on any velocity outside the 0.5-2.0 m/s band.

### Records
- Pipe documents now carry their own ``level`` field in addition to the ``kind``
  discriminator. Reports previously read a field that was never written and
  attributed every pipe to MP; MP/SP/ZP lengths are now reported correctly.
- Duplicate-name detection compared the collection name instead of the record
  label, so it never fired. It is now an anchored, case-insensitive check scoped
  to the project (and to ``kind`` where the domain requires it).
- The estimation result shows the taxable amount, so the subtotal -> discount ->
  taxable -> tax -> total chain can be audited.
- The generic collection API returns a normalised string ``id`` instead of a raw
  ``_id``, matching the geometry endpoint.

## 0.1.0 - 2026-10-13

Initial build of the FreeBuff copy, written from `OPENCODE.md` into an empty
`D:\Projects\FreeBuff\FIS`.

### Platform
- Flask application factory, PyMongo and Flask-Login extension wiring, file
  logging, and JSON-aware 401/403/404/500 handlers.
- Isolated MongoDB database (`fis_freebuff`) so this copy cannot collide with the
  other copies running against the same server.
- Bilingual chrome with English/Arabic strings and right-to-left layout.
- Authentication with Werkzeug password hashing. The first account becomes a Full
  Admin; later self-registrations are Customers. No default password is shipped.

### Navigation
- The mandated main tab order: Project - Water Resource - Geometry - Hydrology1 -
  Trees - Hydrology2 - Estimation - Report, with every specified sub-tab.

### Geometry
- Sectors and zones with add, edit, rename, remove, split, merge, swap and
  generate, plus project-scoped cascade deletes.
- Geodetic measurement via local UTM reprojection; polygon algebra via shapely.
- **Equal-area partitioning**: sectors and zones are sliced until the piece has
  exactly the target area, so three zones are three equal thirds rather than
  50 % / 25 % / 25 % from recursive halving.
- **Explicit coordinate system**: the CRS is resolved once from the project land
  boundary and threaded through every derived polygon. Without it, a 100 m x 50 m
  strip is numerically indistinguishable from lon/lat degrees and its area is
  wrong by about ten orders of magnitude.
- Duplicate record names are refused per project (this check previously compared
  the collection name instead of the record label, so it never fired).

### Engineering
- Hazen-Williams forward and inverse solvers, Darcy-Weisbach with Swamee-Jain,
  minor losses and the Christiansen multi-outlet factor.
- Emitter discharge law with distribution and ASABE EP458 emission uniformity.
- FAO-56 water balance, USDA SCS effective rainfall, FAO-29 leaching requirement
  and pump duty point.
- Row strips carved from sector geometry and trees sampled along row centrelines
  with the plant pattern centred so no tree lands on a boundary.

### Estimation and reporting
- Line items, discount and tax with the full auditable chain
  (subtotal, discount, taxable amount, tax, total) and quantity seeding from the
  drawn design.
- Charts, bilan hydrique and the printable technical report with validation
  findings.

### API
- Read-only JSON for geometry, project summary and child collections, with
  normalised string ids and 401 responses for unauthenticated callers.

### Tests
- 99 tests: geometry, hydraulic, emitter and agronomy engines plus HTTP route
  tests covering authentication, isolation between two accounts, generation,
  split/merge/swap and the estimation totals.
