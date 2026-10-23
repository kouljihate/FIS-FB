# Changelog

## 0.4.17 - 2026-10-23

### Added
- blue-to-red elevation heatmap ramp, used by the Geometry map editor so the lowest ground reads blue and the highest red

### Changed
- the Sectors and Zones map editors load only sector and zone outlines: trees, rows, pipes, water sources and the land boundary no longer appear there
- their drawing toolbar offers the polygon tool only, since a sector or zone is the only shape those pages save
- both Geometry pages draw the project's elevation heatmap under the sector and zone outlines, with a legend on the same blue-red scale

## 0.4.16 - 2026-10-23

### Changed
- every page now paints the trianglify1.png pattern behind its content, softened with a cream veil so text keeps its contrast

## 0.4.15 - 2026-10-23

### Changed
- every workflow tab and sub-tab shows its icon above the English then Arabic label
- tabs and sub-tabs turn brown on hover and keyboard focus

## 0.4.14 - 2026-10-22

### Fixed
- the project tab strip no longer scrolls Map Overview, Estimation and Report out of sight; the tabs wrap onto a second row on narrow windows

## 0.4.13 - 2026-10-22

### Added
- Project tab map draws only the sector layout, the water sources and an elevation heatmap, each with its own toggle.
- elevation heatmap from the cached terrain model, with a gradient legend showing the real min/max and sample count.
- ROI subtab with the 5-year return, payback and yearly cash flow, and assumptions saved per project.
- KML water bodies drawn as polygons (Basin, Bassin, حوض, ...) are imported as water sources at their centroid.
- Map Overview lists water sources per kind (Wells, Basins, Reservoirs, Pumps, Canals) in the Displayed marks panel.

### Changed
- Map Overview opens with the water sources next to the sectors, so a basin is visible without switching its filter on.
- water sources stay on the map in a scoped sector view.
- tree labels are drawn only at a readable zoom and for the trees in view, keeping large orchards responsive.
- water-source markers carry their kind's initial (W, B, R, P, C) instead of a generic dot.

### Fixed
- a KML basin imported as a polygon never reached the Water Sources table; projects imported earlier repair themselves when the Project or Map Overview page is opened.
- a selected feature keeps its label and highlight even when its category's labels are off.

## 0.4.12 - 2026-10-21

### Added
- the Displayed marks panel gives every mark type its own row with its sum, under the grand total

### Changed
- rows keep the funnel order and dim when a type is hidden or not loaded, so the list never re-orders

### Fixed
- the sums stay clear of the scrollbar and the panel fits above the map's bottom edge

## 0.4.11 - 2026-10-21

### Changed
- every map now uses the app fonts (Comfortaa / VIP-Rawy), including zoom controls, popups, tooltips and attribution.
- selecting a sector in Map Overview switches the funnel to Sector + Zones only, so the map opens on that sector's layout.

### Fixed
- leaving a sector restored the filter buttons but not the geometry behind them.
- switching an on-demand category off before its fetch finished brought it back on the map.
- an empty filter selection fetched and drew the whole project instead of nothing.
- fitting buttons (connector, reductor, air removal, ...) read the geometry state for a shared key.

## 0.4.10 - 2026-10-21

### Changed
- each source is coloured by kind (well, basin, reservoir, pump, canal) and its popup shows the kind and flow rate.

### Fixed
- wells, basins and other water sources are all drawn, while the geometry payload only ever emitted the single legacy point.

## 0.4.9 - 2026-10-21

### Added
- geometry API sector scope, which keeps every sector outline but only the marks of the chosen sector.

### Changed
- clicking a sector in the Map Overview list now shows only that sector's zones, rows, trees and network, with the other sectors drawn as dashed boundaries.

### Fixed
- the displayed-marks counter updates when layers are removed and no longer waits for animation frames.

## 0.4.8 - 2026-10-21

### Added
- statistics widget under Selected object showing how many marks the map is currently displaying.

### Changed
- the count follows the map filters, so hiding or loading a category updates it immediately.

## 0.4.7 - 2026-10-21

### Added
- geometry API `?types=` filter, so a map can request only the element categories it draws.

### Changed
- Map Overview opens on sectors only and fetches every other category the first time its filter is switched on, instead of drawing the whole project up front.
- overview assigns short names to sectors only, instead of naming every zone, row and tree on page load.

### Fixed
- popups on lazily loaded layers (zones, rows, trees, pipes, components) resolve their element before opening.

## 0.4.6 - 2026-10-20

### Added
- Map Overview tab - one full-page project map with in-map sector shortcuts, a filter covering every element type and an Edit/Rename/Delete panel for the selected object, starting on sectors only.
- shared component page template, so Connectors, Reductors, Air Removal, Reduce Pressure and Terminators get the same table, modals and map tools as Valves.
- Auto Generate Zones works on a multi-sector map selection, with a per-sector orientation in degrees.

### Changed
- zone and row orientations are stored and applied in exact degrees; rows of a zone follow its bearing.
- geometry projection uses one CRS per shape, so shapes near a UTM zone boundary no longer jump.

### Fixed
- unclosed preload links in the base template swallowed every page's extra head, losing page CSS/JS and the overview map sizing.

## 0.4.5 - 2026-10-19

### Changed
- `app.py` (M)
- `release.py` (M)
- `irrigation/templates/components/air_removal.html` (??)
- `irrigation/templates/components/connectors.html` (??)
- `irrigation/templates/components/reduce_pressure.html` (??)
- `irrigation/templates/components/reductors.html` (??)
- `irrigation/templates/components/terminators.html` (??)
- `irrigation/templates/hydrology2/placeholder.html` (??)
- `irrigation/templates/hydrology2/valves.html` (??)

## 0.4.4 - 2026-10-19

### Added
- the Auto Generate Zones modal lists every sector with its own Auto (from slope) or manual compass orientation choice; each sector's zones are generated with that orientation.

### Changed
- regenerate_zones accepts a sector_orientations map, and the zone AI route reads orientation_<sector_id> fields while still supporting the legacy single orientation value.

## 0.4.3 - 2026-10-19

### Fixed
- Zones/Sectors row modals were nested inside <tbody>, so the browser foster-parented them and their forms were emptied - Edit, Orientation, Rename and Split did nothing. Modals now render outside the table.
- Auto Generate Zones now stores orientation_deg matching a forced compass orientation instead of the terrain slope angle.

## 0.4.2 - 2026-10-19

### Added
- compass button in the Zones Actions column, right after Edit, opening an orientation modal in degrees
- POST /zones/<id>/orientation storing the angle exactly and rounding it to the nearest of the 32 compass points, with a live nearest-point preview

### Changed
- Zones Orientation (°) column and Edit modal show the exact stored angle, kept in sync with the compass list
- removed the degrees-orientation button, modal and route from Sectors

## 0.4.1 - 2026-10-19

### Added
- compass button in the Sectors Actions column, right after Edit, opening an orientation modal in degrees
- POST route storing the angle exactly and rounding it to the nearest of the 32 compass points, with a live nearest-point preview in the modal

### Changed
- Orientation (°) column and the edit modal show the exact stored angle, kept in sync when the compass list is used

## 0.4.0 - 2026-10-19

### Added
- JSON-schema validators and indexes for `user` and `project`, created automatically at startup
- profile page (details, photo, password) and an Admin-only users/role page
- `DATABASE.md` describing the model and its limits
- Create Project now links each project to a Project Admin (`admin_user_id`), with a modal to create one when no Project Admin exists yet
- Delete button next to Edit on the project page, deleting the whole project after a modal confirmation
- Admin-only Users tab placed first in the navigation, listing every user with their profile and active state
- Owner column in the Projects table, resolved with a single lookup per page

### Changed
- new two-collection MongoDB model on the `FIS` database: `user` and `project`
- land map, water sources, sector -> zone -> row -> tree geometry, pipes, components, piping reports and validations now live inside the project document
- user records carry full name, phone, CIN, photo and profile; login accepts a username or an email
- role enforcement for Admin, Project Admin and Project Owner across pages, the JSON API and file downloads

### Fixed
- KML export with the pinned fastkml 1.0 API
- the estimation page failing on its custom-items list

## 0.3.85 - 2026-10-19

### Changed
- the Valve Name column is gone; Short Name shows just the code (VM1, VS1, VZ1)
- Type, Assigned To and the totals line show Arabic on top and English underneath

### Fixed
- valves remain searchable by name through a hidden data attribute

## 0.3.84 - 2026-10-19

### Added
- Short Name column (VM1 / VS1 / VZ1 with the valve name) and searchable short names
- valve-type filter for the map and the table (VM / VS / VZ)

### Changed
- valve and geometry filters share one left-hand map widget so they can no longer overlap

### Fixed
- the duplicate label widget in the map's bottom-right corner
- adding a valve after a deletion reuses a free number instead of duplicating one

## 0.3.83 - 2026-10-19

### Added
- Generate Valves - places master, sector and zone valves on the pipe junctions using each pipe's diameter (upstream -> valve), with warnings for sectors or zones that have no pipe
- Edit Valve action and modal (type, assignment, diameter, location, names)

### Changed
- Valves toolbar order is now Add, Edit, Rename, Delete, Generate, Validate
- the Add Valve card is replaced by a modal that can pick its point on the map
- Valves table shows the diameter column (upstream pipe -> valve)

## 0.3.82 - 2026-10-19

### Added
- release.py --transport auto|ssh|https; the successful transport is remembered so later releases skip denied credentials

## 0.3.81 - 2026-10-19

### Added
- release.py --push-only to retry a failed push, with an automatic HTTPS fallback when the SSH key is denied

### Changed
- OPENCODE.md documents the release workflow that must follow every change prompt

### Fixed
- double blank line between CHANGELOG entries

## 0.3.80 - 2026-10-19

### Added
- Components section with six sub-tabs (Valves, Connectors, Reductors, Air Removal, Reduce Pressure, Terminators) on their own /components URLs
- map filter widget that hides sectors, zones, rows, trees and pipes
- release.py to bump the version, sync the markdown files, commit and push to FIS-FB

### Changed
- Valves page rebuilt as a Rows-style table with filter, rename and per-row actions

## 0.3.78 - 2026-10-18

### Added
- **Components Tab**: new main navigation tab with 5 sub-tabs:
  - **Valves / الصمامات** - existing valves management
  - **Connectors / الوصلات** - placeholder (coming soon)
  - **Reductors / المخفضات** - placeholder (coming soon)
  - **Air Removal / إزالة الهواء** - placeholder (coming soon)
  - **Terminators / النهايات** - placeholder (coming soon)

## 0.3.77 - 2026-10-18

### Fixed
- **Trees/Trees pagination**: added 50-rows-per-page pagination with page navigation controls and configurable page size (25/50/100/All).
- **Trees/Trees filter/search**: fixed to match both Short Name and Name, works correctly with pagination.
- **Trees/Trees counter badge**: now shows "selected / total" where total = ALL trees (not just visible). Works correctly with pagination.
- **Trees/Trees select all**: works correctly with pagination and filtering.

## 0.3.69 - 2026-10-18

### Fixed
- **Trees/Trees counter badge**: now shows "selected / total" where total = ALL trees (not just visible). E.g. "0/5968". Select-all checkbox syncs with total count.

## 0.3.68 - 2026-10-18

### Fixed
- **Add Tree modal**: row dropdown now correctly populates from API FeatureCollection (filters `type: 'tree_row'` features by zone).

### Added
- **Trees/Trees Edit button**: enabled when exactly 1 tree selected; opens Edit Tree modal with pre-filled data (name EN/AR, variety, drips, spacing).
- **tree_edit route**: new POST route to update a single tree record.

## 0.3.67 - 2026-10-18

### Fixed
- **Trees/Trees filter/search**: now matches both Short Name and tree Name; properly shows/hides rows.
- **Trees/Trees counter badge**: fixed to count visible rows correctly; updates on load, search, checkbox toggle, sort, and select-all.
- **Trees/Trees select all**: now only affects visible (non-filtered) rows; select-all checkbox state syncs with selection count.

## 0.3.66 - 2026-10-18

### Fixed
- **Trees/Trees counter badge**: simplified `updateTreeBadge()` to count visible rows only (removed redundant short-name re-match). Badge now correctly shows selected/total on load, search, checkbox toggle, and sort.

## 0.3.65 - 2026-10-18

### Fixed
- **Tree name format**: changed default name from `Tree {id}-{n}` to `T{id}-{n}` (e.g. `T6576-1`) in both `trees_generate` and `tree_add` routes.

## 0.3.64 - 2026-10-18

### Added
- **Trees/Trees toolbar behavior**: Edit button enabled only when exactly 1 tree selected; Delete button enabled when 1+ trees selected; both disabled when none selected. Add (+) button always enabled, opens Add Tree modal.
- **Add Tree Modal**: new modal to add a single tree with Zone, Row, Name EN/AR, Lng/Lat, Variety EN/AR, Drips/Tree, Spacing.
- **tree_add route**: new POST route to insert a single tree record.

## 0.3.63 - 2026-10-18

### Removed
- **Trees/Trees table**: removed Generate circle button from header action row.

## 0.3.62 - 2026-10-18

### Changed
- **Trees/Trees table**: all header columns and all data cells set to center alignment (text-center).

## 0.3.61 - 2026-10-18

### Changed
- **Trees/Trees table**: removed Variety (AR) column.
- **Trees/Trees tree name**: changed format from `Tree {id}-{n}` to `T{id}-{n}` (e.g. `T6576-1`).
- **Trees/Trees Row column**: set to center alignment.
- **Trees/Trees ordering**: applies to all trees (removed 100-row limit and "Showing first 100" notice).
- **colspan values** updated from 10 to 9 after removing Variety (AR) column.

## 0.3.60 - 2026-10-18

### Fixed
- **Trees/Trees filtering & ordering**: search now filters by Short Name only (`data-short-name` attribute); Short Name header is clickable to sort ascending/descending with sort icon.
- **Trees/Trees Validation button**: fixed to POST via form submission (route returns redirect, not JSON).

## 0.3.59 - 2026-10-18

### Fixed
- **Trees/Trees Validation button**: added click handler to POST to `trees.trees_validate` route (was missing handler).

## 0.3.58 - 2026-10-18

### Fixed
- **Trees/Trees counter badge**: now updates on checkbox toggle (not only on search input change) via `updateTreeBadge()` called from both `updateTreesToolbar()` and `applyFilter()`.

## 0.3.57 - 2026-10-18

### Fixed
- **Tree Setup Generate button**: reliably disabled when All Sectors / Zones or any Zone Card percentages exceed 100%.

### Added
- **Tree Setup Zone card header**: split into 3 parts — Short Name (left), total percentage (middle, turns red when over 100%), Rows count (right). Percentage updates live as inputs change.

## 0.3.56 - 2026-10-18

### Added
- **Tree Setup sync**: entering values in the All Sectors / Zones card (Spacing, Variety 1/2/3, Pct 1/2/3) reflects into all Zone cards automatically.

## 0.3.55 - 2026-10-18

### Changed
- **Tree Setup validation**: Generate Trees button is now disabled (instead of showing a toast) when All Sectors / Zones or any Zone Card variety percentages exceed 100%. Button re-enables when percentages are corrected.

## 0.3.54 - 2026-10-18

### Added
- **Tree Setup validation**: client-side percentage check on Generate Trees submit — blocks submission and shows a toast if All Sectors / Zones or any Zone Card variety percentages exceed 100%.

## 0.3.53 - 2026-10-18

### Changed
- **Tree Setup - Zone cards inside Sector cards**: changed from col-md-6 to col-md-4 so 3 zone cards display per row instead of 2.

## 0.3.52 - 2026-10-18

### Changed
- **Tree Setup - All Sectors card**: colored header (bg-primary text-white), split into two rows (Spacing + Drips on row 1; all 3 Variety/Pct pairs on row 2) with shorter col-md-2 fields.
- **Tree Setup - Zone card header**: replaced zone Name with Short Name (e.g. `S1Z1`), kept Rows count.
- **Tree Setup - Zone card Variety/Pct fields**: reduced field width from col-md-6 to col-md-3.

## 0.3.51 - 2026-10-18

### Added
- **Geometry/Zones Short Name**: added Short Name column (e.g. `S1Z1`) to the Zones table header and body rows.
- **Pipes Short Name**: added Short Name column (MP1/SP1/ZP1) to Main Pipe, Second Pipe, and Zone Pipe tables.
- **Search zone CSS class**: renamed `zone-search`/`tree-search` filter component classes to generic `search-zone` (search-field, search-input, search-suffix, search-row-count, search-clear) and applied to Trees/Trees and Geometry/Zones table filters.

### Changed
- **Geometry/Zones filter**: wrapped the zone name search input in the `search-zone` input-group structure for consistent styling.

## 0.3.50 - 2026-10-17

### Changed
- **Tree Setup modal zone cards**: removed Arabic variety fields (Variety 2/3 AR, Pct AR variants); each variety row now shows only Variety N (EN) + Pct N (%).

## 0.3.49 - 2026-10-17

### Changed
- **Tree Setup modal zone cards**: each Variety/Pct pair now on its own row (Spacing row; Variety1+AR+Pct1 row; Variety2+AR+Pct2 row; Variety3+AR+Pct3 row), instead of all fields in one crowded row.

## 0.3.48 - 2026-10-17

### Changed
- **Tree Setup modal sector cards**: added collapse/expand button (chevron icon) at the extreme right of each sector card header to toggle the sector body.
- **Tree Setup modal zone cards**: reduced zone fields to Spacing, Variety 1 (EN/AR), Pct 1, Variety 2 (EN/AR), Pct 2, Variety 3 (EN/AR), Pct 3 — laid out in a single row per zone card. Removed Drips/Tree input (defaults to 2 server-side).

## 0.3.47 - 2026-10-17

### Changed
- **Tree Setup modal bigger**: `#treeSetupModal` max-width increased to 1000px (95% width) for more room on setup cards.

## 0.3.46 - 2026-10-17

### Fixed
- **Tree Setup button**: added missing `#treeSetupModal` HTML (was referenced by the button but never defined). Modal posts to `trees.trees_generate` with an "All Sectors / Zones" shared setup card plus per-zone setup cards grouped by sector, each with spacing, drips, and variety fields matching the `trees_generate` route form expectations.

## 0.3.45 - 2026-10-17

### Added
- **Trees table Delete All button**: circular trash2 button in the Trees table header action row opens a confirmation modal and deletes all trees at once (no `tree_ids` → `delete_many` across the whole project).

### Changed
- **Trees table headers**: restructured to 3 rows mirroring the Trees → Rows table — row 1 has circle action buttons (Tree Setup, Add, Edit, Remove, Delete All, Generate, Validate), row 2 has the search input with selected/visible badge and clear button, row 3 keeps the column labels (ID, Name, Row, Short Name, Variety EN/AR, Spacing (m), Drips/Tree, Validated).
- **Tree Setup button**: restored and displayed inline in the table header action row, targeting `#treeSetupModal`.
- **Trees table ordering**: trees sorted by zone → row → short_name → id for hierarchical ordering (`S1Z1R1T1`, `S1Z1R1T2`, …).
- **Tree Setup button**: removed the Generate Trees from Rows card; tree generation moved to the table header Generate button via the Tree Setup modal.
- **Delete All route**: `trees_remove` now deletes all trees for the project when no `tree_ids` are posted.

## 0.3.44 - 2026-10-16
### Added
- **Trees table Short Name column**: Trees -> Trees table now shows each tree's hierarchical short name (e.g. `S1Z1R1T1`) in a `<code>` cell, sourced from `assign_short_names`.
### Changed
- **Trees table headers bilingual**: Trees -> Trees table header now matches the Trees -> Rows header style (Arabic + English stacked, centred) across ID, Tree Name, Short Name, Row, Variety (EN/AR), Spacing (m), Drips/Tree and Validated, with centred, middle-aligned body cells.

## 0.3.43 - 2026-10-16

### Added
- **Rows zone search input**: Trees → Rows filter row Zone dropdown replaced with a live text input; typing a zone short code (e.g. `S1Z`, `Z1`), a zone id, or an English/Arabic name filters the rows live, with a clear (×) button.
- **Rows selection counter badge**: rounded pill inside the zone-search input shows selected / visible rows (`0/24`, `n/N`) and switches colour once rows are selected.
- **Tree Setup "All Sectors / Zones" card**: first card in the Tree Setup modal; filling it live-propagates the shared spacing, drips and variety split to every zone card.

### Changed
- **Rows table ordering**: rows are now always sorted ascending by Short Name (natural order, `R2` before `R10`), computed server-side in `trees_view`/`rows_view` via `_short_name_key`.
- **Tree Setup modal redesigned**: zones are now cards (header with EN/AR name, short name, Rows, Spacing, Drips and live Σ%) laid out in a scrollable auto-fill grid grouped by sector with sector labels, replacing the single wide multi-column table. Zones are ordered by zone short name, and the "All Sectors / Zones" card is pinned first.
- **Generate button no longer gated on full configuration**: enabled whenever at least one zone has rows; zones left unset fall back to the shared setup or legacy defaults. Progress text reports `N / N zones ready`, shared-setup, or "unset zones use defaults".
- **Tree Setup modal init ordering**: `setupTreeSetupModal()` now runs before `initMap`, and `initMap` is wrapped in try/catch, so a map error can no longer leave the Generate button disabled.

## 0.3.42 - 2026-10-13

### Added

- **Pipe rules cards**: the Pipes sub-tabs (Main Pipe, Second Pipe, Zone Pipe) now show a collapsible grey "Rules" card (centred header button toggles the body) listing every applicable rule (id, English/Arabic name and description) sourced from `engineering.hydraulics`.
- **Link (KML) shortcut**: the project list gained a circular link button in the Actions column (enabled only for projects without an attached KML) that opens the per-project KML-linking dialog.
- **Rows table zone filter**: the Trees → Rows table gained a filter row (between the actions row and the column titles) with a Zone dropdown; selecting a zone hides the other rows.
- **Zones empty state**: when a project has no zones at all, the Geometry → Zones table now shows a centered full-width "No Zone Yet" banner row.
- **Zones name filter**: the Geometry → Zones table gained a filter row (between the actions row and the column titles), split into three parts with a centered search box in the middle; it filters the rows by zone name (English or Arabic).

### Changed

- **Project tab simplified**: removed the New / Load / Link sub-tab rail; the Project tab is now a single link to the project list, and its sub-pages remain reachable through the list.
- **Coming Soon badges removed** from the navigation; placeholder items are now distinguished only by their tooltip and disabled state.
- **Zones table fits the page width** (no horizontal scroll): fixed layout with explicit column widths and wrapped content; sector table headers are centred.
- **Second Pipe stage builds second pipes only**: `scope='second'` no longer creates diameter reductions (they belong to the zone stage) and its replace step now removes only second pipes, leaving reductions untouched. The Second Pipe rules card and generate dialog reflect the `MP → SP` link without the reduction step.
- **Second Pipe rules clarified and enforced**: `SECOND_PIPE_RULES` now states SP-2 (starts on the Main Pipe, never at the MP extremity), SP-3 (stays on the sector boundary only) and SP-4 (terminates at the entry of its respective Zone). The generator routes each SP along the sector ring from the Main Pipe tap point to the Zone entry, instead of a straight line.
- **Pipe tables simplified with a Total row**: the Main Pipe, Second Pipe and Zone Pipe tables now share one column set — checkbox, `ID`, `Name / الاسم` (English + Arabic stacked), `Diameter / قُطْر`, `Length (m) / طول`, `Status / حالة` and `Action / إجراءات` — and sum the pipe lengths in a footer row (`Total / المجموع`). The separate Name (EN/AR), Flow, Velocity and Zone columns were removed.

### Fixed

- **Trees tab generated nothing**: the row tracer anchored each parallel line at the coordinate origin instead of the polygon centroid, so with real projected (UTM) coordinates the lines never crossed the zones and Generate produced zero rows — and therefore zero trees. The traced lines are now centred on each polygon's centroid, so Rows and Trees generation work.

## 0.3.40 - 2026-10-13

### Added

- **Water Resource workflow tab**: a new top-level section with **Well**, **Basin** and **Other** sub-tabs. A new `water` blueprint serves `/water/<project_id>/well`, `/water/<project_id>/basin` and `/water/<project_id>/other`, rendering the centred "Coming Soon" placeholder pages inside the app layout.
- **Terrain-derived sector orientation**: regenerated sectors now record an `orientation` compass point and `orientation_deg` bearing from the elevation model's downhill aspect, matching the per-zone behaviour.
- **Coming Soon placeholders**: sub-tab rails mark not-yet-built items (Estimation **Result**, Report **Charts**, Components **Mandatory**/**Optional**) with a "Coming Soon" badge.

### Changed

- **Workflow navigation restructured** into eight tabs and their sub-tabs:
  - **Project**: New / Load / Link
  - **Water Resource**: Well / Basin / Other
  - **Geometry**: Sectors / Zones
  - **Pipes**: Main Pipe (MP) / Second Pipe (SP) / Zone Pipe (ZP)
  - **Trees**: Rows / Trees
  - **Components**: Mandatory / Optional (Coming Soon)
  - **Estimation**: Pricing / Result
  - **Report**: Charts / Standard / Technical (DPA)
- Second Pipe (SP) moved from a top-level tab into the **Pipes** sub-tabs; the former **Hydrology 2** (Valves / Dripline / Network) is no longer shown in the navigation (its routes remain reachable directly).
- **Eight-point zone orientation**: `regenerate_zones` now accepts the full compass set (`N`, `NE`, `E`, `ES`, `S`, `SW`, `W`, `WN`), so non-cardinal directions chosen in the modal are no longer silently dropped.
- Renamed the "AI regenerated" flash messages to "Auto regenerated" for consistency with the Auto Generate UI; the sub-tab rails are now always centred.

## 0.3.38 - 2026-10-12

### Added

- **In-map delete on every map**: Sectors, Zones, Trees, Rows, Valves, Network and Dripline maps now show a circular trash button in the map toolbar (alongside the existing Main/Zone Pipe trash). It stays disabled until at least one item is selected — via the table checkboxes or by clicking features on the map with Select mode on — then opens the styled delete-confirmation modal.
- **New remove endpoints**: `trees.trees_remove` (`tree_ids`) plus `hydrology.valves_remove`, `hydrology.dripline_remove` and `hydrology.network_remove` (`element_ids`) delete the selected records and flash a removal count.
- **Second Pipe (SP) step**: New Hydrology 1 page between Main Pipe and Zone Pipe with bilingual table, map, Add Second Pipe modal, and Auto Generate Second Pipes (MP → reduction → SP → reduction → ZP). Rules SP-1/SP-2 stored and applied.
- **Zone Pipe rules refinement**: ZPs routed along sector boundary (ring path), strictly on boundary, driplines tap from inside; reductions generated in `scope='zone'`; max vertex distance from boundary `6.6e-9°`.

### Changed

- Trees, Valves, Network and Dripline tables gained a selection checkbox column (plus a select-all and a Remove button) so their maps can drive the new in-map delete.
- `initMap` now accepts a `selectable.type` array, letting a single map select several element types (all valve types, all network-element types), and a new `mapDelete` option that renders the selection-gated trash button.

## 0.3.39 - 2026-10-12

### Added

- **Second Pipe (SP) UI**: full page between Main Pipe and Zone Pipe with table + map + Add modal + Auto Generate modal; nav updated in `base.html`.
- **Geometry Sectors/Zones tables**: bilingual 2-row header (Arabic top / English bottom) with circle-icon action buttons row; first header cell given `min-width` constraint.
- **Sorting by name**: Sectors and Zones queries now sort by `name_en` ascending instead of `id`.
- **API color**: `second_pipe` element color `#0d6efd` added to `_get_elem_color`.

### Changed

- PIPE_TYPES extended to `('main_pipe', 'second_pipe', 'sub_pipe')`; new routes `/second_pipe`, `/second_pipe/add`, `/second_pipe/validate`.
- `_build_boundary_network` accepts `build_second` parameter; SP edges (`kind='sp'`, `type='second_pipe'`) built from Main Pipe to nearest Zone boundary entry.
- `generate_piping_structure` handles `scope='second'` with correct reductions filter and delete block.
- Second pipe validation toggles `validated` flag on all `second_pipe` elements.
- README version badge and feature list updated.

## 0.3.37 - 2026-10-12

### Added

- **Map header with full-page toggle**: every map (Project, Sectors, Zones, Trees, Rows, Main/Zone Pipe, Valves, Network, Dripline, Elevation Heatmap) now shares one header layout split into three columns — English title (20%) on the left, a circular fullscreen button in the centre, and the Arabic title (20%) on the right. The button expands the map to fill the viewport (Esc exits) and restores it afterwards.
- **Zone orientation**: zones now carry an `orientation` compass field (N/E/S/W). It can be set manually in the Add/Edit Zone modals, and the Auto Generate Zones dialog lets you either derive it per zone from the terrain or force a direction.

### Changed

- **Sectors & Zones tables**: the Sectors list is now `checkbox · ID · Name/الاسم · Valid/التحقق · Area (m²)/المساحة · Percentage (%) · Actions`, and the Zones list is `checkbox · ID · Name/اسم · Valid/التحقق · Area (m²)/المساحة · Percentage (%) · Orientation · Actions`. A new **Percentage (%)** column shows each element's area as a share of the total project (land) area.
- **Slope-aware zone generation**: Auto Generate Zones now also considers elevation contour bands (fall-line partitioning) alongside the angle-based partitions, so zone orientation follows the terrain. Each generated zone records its orientation, downhill bearing and slope percentage derived from the elevation model.

## 0.3.36 - 2026-10-12

### Added

- **Styled delete-confirmation modal**: every delete action across the app (projects, sectors, zones, rows, main/zone pipes) now uses a dedicated confirmation modal — red header with the Arabic title "تأكيد الحذف" (two-column header, white text), a body on `#DD99BB` with the English and Arabic sentences and the object name highlighted in bold, and rounded red Confirm / Cancel buttons.
- A reusable `window.confirmDelete(nameEn, nameAr, onConfirm)` helper plus delegated `data-confirm-delete` / `data-confirm-delete-ar` handling for forms and submit buttons.

### Changed

- Delete triggers were rewired to the new modal: sector/zone/row selection removals and pipe removals now pass the selected object names, the project delete form and single-row delete form use `data-confirm-delete`, and the hidden pipe-remove form no longer double-confirms. Non-delete confirmations (AI generate, rebuild, clear) keep the existing generic modal.

## 0.3.35 - 2026-10-12

### Changed

- **Main Pipe AI generation is now scoped**: generating from the Main Pipe page builds **only the Main Pipe** — a single element (a `MultiLineString` when the spine branches) covering just the highest sectors — instead of the whole network. Zone pipes, driplines, reductions, valves and air releases are no longer created there; they are produced by the Zone Pipe step.
- **Zone Pipe AI generation is now scoped**: generating from the Zone Pipe page builds **only the zone pipes and driplines** and leaves the existing Main Pipe untouched, so the staged workflow (Main Pipe → Zone Pipes → …) no longer overwrites earlier steps. The generator accepts a `scope` option (`full` | `main` | `zone`) and only deletes the elements relevant to that scope.

### Fixed

- **Connectivity / elevation validation with a merged main pipe**: the water-source connection and elevation-head checks now handle the `MultiLineString` main pipe (a line-endpoint helper is used instead of assuming a single `LineString.coords`).

## 0.3.34 - 2026-10-12

### Added

- **Report tabs**: the Engineering section gains an **Engineering Validation** and a **Technical Report** sub-tab; a new route (`engineering.technical_report_view`) renders a bilingual technical report combining the last validation and regeneration report, and the base subnav is updated to link both.

### Changed

- Documentation sync: README version badge updated from 0.3.25 to the current release and its feature list expanded to cover the present workflow (AI generation for sectors/zones/piping, elevation-aware design, multiple water sources with KML import, Rows workflow, estimation and land documents).

## 0.3.33 - 2026-10-09

### Fixed

- **AI Generate Sectors ignored elevation**: two root causes fixed. (1) A failed or empty elevation fetch was still treated as "usable" (and cached forever), so the elevation scoring terms collapsed to neutral constants and the elevation toggle had no effect. Elevation is now only considered usable when the model contains real samples/stats; empty cached models are discarded and rebuilt, and models without data are no longer cached. (2) The partition search only produced straight equal-area strips, which spread each strip across the full elevation range, so the elevation criteria could not discriminate between candidates.

### Added

- **Elevation contour-band sectorization**: when real terrain data is available, sectors are now partitioned into equal-area bands that follow the dominant ground gradient (fall line/contours) — cells inside the boundary are projected onto the fitted elevation-gradient direction and cut into equal-area bands. This candidate is scored alongside the strip partitions and wins when terrain makes it better, producing terrain-aware sector positions (low → high elevation). Flat terrain or missing data falls back to the existing strip strategy.

## 0.3.32 - 2026-10-09

### Added

- **KML/KMZ upload (Project page)**: the uploaded-file indicator is now a clickable button containing a badge with the total element count; clicking it opens a modal form that enumerates every element inside the file (name, category, geometry type plus per-category counts: boundary, sectors, water sources, overlays).
- **Water sources from KML**: every Point/MultiPoint feature in an uploaded KML/KMZ file is now imported into the project spec `water_sources` list (classified by name into well / basin / reservoir / pump / canal / other), so the Project Water Sources table and spec-based flows are populated. Imports are deduped by name + position across re-uploads, notes record the source file, and the legacy single `project.water_source` Point stays in sync with the first imported source.

### Changed

- **KML feature classification**: point features are categorised as `water_source` (all points), polygons as `boundary`/`sector`/`overlay` as before; `water_feature` single-point detection was replaced by the full water-source list.

## 0.3.31 - 2026-10-09

### Added

- **Rows page (`trees.rows_view`)**: the Tree Rows page is revamped into the dedicated Rows workflow tab (its sub-tab bar with Trees is now centred like Geometry/Hydrology 1). The Add Tree Row card / Back button were removed and the page title is now just "Rows / الصفوف".
- **Generate Rows modal**: a **Generate** circular button in the table header opens a modal form asking for the **distance between rows (m)** plus an *Replace existing rows* toggle. It traces parallel rows inside every zone along the zone's dominant axis at the given spacing (`trees.rows_generate`).
- **Rows table action row**: a first header row with icon-only circular buttons for Edit, Rename, Remove, Generate and Validate, mirroring the Sectors/Zones pattern (selection-aware enable/disable).
- **Row selection & Actions column**: a new checkbox column (with select-all) selects rows; a new centred **Actions** column provides per-row Edit, Rename and Remove buttons via modal forms.
- **New row routes**: `trees.rows_edit` (edit names, spacing, orientation), `trees.rows_rename`, `trees.rows_remove` (multi-delete via `row_ids`) and `trees.rows_generate` (auto row tracing, replaces existing rows when requested).

### Changed

- **Rows page**: the Zone, Spacing (m), Orientation, Validated and Actions columns are now centred.
- **Rows page**: row names are resolved to their zone names (EN + AR) instead of the raw zone id.
Auto Generate
## 0.3.30 - 2026-10-09

### Changed

- **Sectors / Zones / Main Pipe / Zone Pipe pages**: removed the above-table toolbar buttons; all actions are now driven exclusively by the icon-only circular buttons in the table header action rows. The Zones header row gained Rename and Edit buttons (moving the batch Rename/Edit into the table), and the Sectors/Zones Remove buttons are now wrapped in their removal forms.
Auto Generate
## 0.3.29 - 2026-10-09Auto Generate
Auto Generate
### Added

- **Main Pipe and Zone Pipe tables**: a first header row with icon-only circular buttons for Add, Edit, Rename, Remove, AI Generate and Validate, mirroring the Sectors/Zones action rows. The Add/Edit/Rename/Remove buttons share the pipe editor's selection-aware enable/disable logic (`pipe_editor.js` now toggles all matching selector buttons, and exposes `openPipeEdit` for the Edit button).

## 0.3.28 - 2026-10-09

### Added

- **Zones page toolbar**: Add / Rename / Edit / Remove buttons with selection-aware enable/disable (0 selected disables Rename, Edit and Remove; 1 selected disables Add; 2+ selected disables Add, Edit and Rename). AI Generate and Validate are pinned to the right side and stay enabled.
- **Zones table action row**: a first header row with icon-only circular buttons for Add, Remove, Split, Merge, Swap, AI Generate and Validate, plus new Split Selected and Swap modals wired to `zone_split_selected` / `zone_swap`.
- **AI Generate Zones modal**: opens a modal form with configurable Zones per Sector (1–8, default from project spec) and a Auto Generateon data toggle; `regenerate_zones` now accepts `use_elevation`.
- **Zone removal route** `POST /<project_id>/zones/remove` (`geometry.zone_remove`): deletes the selected zones together with their tree rows, trees and network elements, mirroring `sector_remove`.

### Changed

- **Sectors / Zones tables**: the Validated and Area (m²) columns are now centred (headers and data cells).

## 0.3.27 - 2026-10-09

### Added

- **Sectors table action row**: a new first header row with icon-only circular buttons for Add, Remove, Split, Merge, Swap, AI Generate and Validate, mirroring the functionality of the existing toolbar buttons.

## 0.3.26 - 2026-10-09

### Added

- **Hydrology-1 map editor** (Main Pipe / Zone Pipe pages): in-map Leaflet controls (Add, Edit/Change, Rename, Remove, plus Trace Main/Zone Pipe on the Zone Pipe page) wired to the shared editor modals (`hydrology/_pipe_editor_modals.html`) and `static/js/pipe_editor.js` that keep the two pages consistent.
- Tables on both Hydrology-1 pages now start with a select-all checkbox column (live-synced with map selection) and end with an Actions column (per-row Rename and Remove buttons). Row checkboxes carry `data-coords` / `data-diameter` / `data-name-*` so Edit and Rename prefill without extra requests.
- Generic pipe routes `POST /<project_id>/pipe/<pipe_type>/rename|edit|remove` (`hydrology.pipe_rename` / `pipe_update` / `pipe_remove`) for both `main_pipe` and `sub_pipe`; `pipe_update` re-derives length from the new coordinates.

### Changed

- Main Pipe / Zone Pipe workflow tabs are now centred like the Geometry tabs (`project-workflow-subnav-centered`).
- `main_pipe` view now also passes `zones` so the shared editor can add Zone Pipes from the Main Pipe page.
- **Auto Generateage toolbars**: Rename and Remove buttons added next to Add (selection-aware: 0 selected disables both, 1 selected enables both and disables Add, 2+ selected enables only Remove); Generate and Validate are pinned to the right end and stay enabled. The in-map control on the Main Pipe page is reduced to Add / Edit / Rename / Remove (Trace MP / Trace ZP removed via the `traceButtons: false` option); the same pipe editor applies the selection enable/disable rules to its map buttons.
- **Table ↔ map are now linked both ways** on the Main Pipe and Zone Pipe pages: pipes are selected/deselected directly by clicking them on the map (`selectable.toggleOnClick`, no Select-mode cursor required) while any table checkbox change still highlights/clears the matching map layer. Removing selected pipes (toolbar, row action, or map trash) is now an AJAX delete (`X-Requested-With: XMLHttpRequest` → JSON response): the table rows, the checkboxes, the select-all state and the map layers are updated in place, so removals are reflected into the map immediately.
- Replaced the global loading spinner (`img/loading.gif`) with a modern minimalist royal-blue circular loader with a transparent background, sized to the light overlay styling in `base.html`.
- **Create Project page**: removed the two-column layout; the Instructions card now sits full-width above the Create New Project form card, with its instructions shown as side-by-side English (left) and Arabic (right) columns.
- **Geometry tables**: Sectors and Zones now include an Area (m²) column, computed from each polygon via a projected UTM area when not already stored.
- **Piping generator rules**: the Main Pipe and Zone Pipe networks are routed along sector boundary edges using a boundary-graph Steiner heuristic with elevation-weighted cost and automatic reconnection of disjoint components (Rules 3 & 4); diameter floors are applied per pipe level and Rule 5 is enforced so the Main Pipe always carries the largest diameter in the network.

## 0.3.25 - 2026-10-09Auto Generate

### Added

- **AI Generate Piping Structure** (`irrigation/engineering/piping.py`): one-click hierarchical network generator with full hydraulic design. Main Pipe runs from the declared water source (well/basin) to every sector centroid (minimum-spanning-tree layout), Zone Pipes run from the nearest Main Pipe tap to every zone centroid, and each tree row is served either directly by a driptube from the nearest Main/Zone Pipe tap (`direct`) or through a Lateral Pipe into a driptube (`lateral`). Generates and persists:
  - Main / Zone / Lateral pipes and driptubes with PVC diameter auto-sizing (flow-based `recommended_diameter_mm`, configurable max velocity per pipe type and diameter floors),
  - master valve at the source, sector valves and zone valves at their pipe junction nodes,
  - reductions at every diameter-change point, an air-release valve at the highest elevation point when the terrain range ≥ 3 m (elevation mode), and check valves plus a stub branch for each secondary water source,
  - coverage report (rows / trees / zones served), total demand vs source capacity, min emitter pressure, max velocity, and per-type length and diameter schedules.
- Pressure design loop: after flows are computed from zone/tree demand, emitter pressures are traced from the source head and the worst-loss edge on any failing path is upsized; a bilingual warning reports how many emitter points remain below the required pressure (booster pump required) and how much the demand exceeds source capacity.
- `POST /<project_id>/piping/generate` route (optionally `replace` = wipe previous AI/structural network, `use_elevation` = include terrain head). Generation report is stored in the `piping_reports` collection and shown as KPI cards on the Main Pipe and Zone Pipe pages.
- Main Pipe and Zone Pipe pages redesigned to the Geometry section layout: bilingual page header, KPI report card, add/draw/validate toolbars, element table (diameter, flow, velocity, length), map, and the AI generate modal.

### Changed

- **Piping rules** (`irrigation/engineering/piping.py`): the Main Pipe is now routed along shared sector-boundary edges (a Steiner tree over the boundary graph, so no pipe leaves the sector polygons) instead of straight centroid-to-centroid lines. The main pipe starts at the water source, serves sectors only (zone pipes branch at a single sector entry point), is biased toward the highest ground via an elevation-weighted cost, and does not descend into low sectors: a sector whose entry is below the highest entry by a tolerance (`main_spine_tolerance_m`, default ~20% of the elevation range) is fed by a branch Zone Pipe dropped from the high main line. The main pipe always keeps the largest diameter (enforced after sizing and after the pressure loop).
- Tap splitting in the network builder now keeps the original far endpoint (the right half no longer collapses into a self-loop), and split fragments are merged back into a single element per original pipe at write time, so counts reflect real pipes (e.g. 8 main + 16 zone) instead of every tap fragment.
- Validation reads stored `flow_lps` for generated pipes in hydraulic and elevation-head checks; laterals (row-attached sub pipes) may connect to a Zone Pipe instead of only the Main Pipe.
- Piping workflow labels now read Main Pipe (MP) / Zone Pipe (ZP); `reduction` is a first-class element type in the API color map, estimation item types, map rendering and the network table.
- `reduction` point color `#198754` added to the API and map legend; estimation labels for sub pipes renamed to "Zone / Lateral Pipe".
- `projected_shape`/`transform_coords` build the UTM transformer once per geometry and the UTM CRS is cached (`@lru_cache`), dramatically speeding up validation and map geometry calls on large networks.
- Connectivity validation uses an STRtree spatial index over projected Main Pipes instead of quadratic point-to-line distances.

### Fixed

- Reducing flanges counted incorrectly (a `dict(edge)` copy after endpoint mutation produced self-loop edges that disconnected the rest of the network). Verified via AST check, template rendering, test-client route smoke tests, and an end-to-end generation + full validation run on a real project (454 rows / 10,312 trees).

## 0.3.24 - 2026-10-08

### Added

- **Water Sources card** on the project page: lists all declared water sources with a type badge (Well / Basin / Reservoir / Pump / Canal / Other, bilingual EN/AR), name, coordinates, flow rate (L/min) and notes; shows an empty-state row when none exist.
- **Auto Generatees section** in the Edit Project modal: dynamic add/remove rows for multiple sources, each with type, name, lat, lng, flow (L/min) and notes. Saved into `spec.water_sources`.
- Legacy single `project.water_source` Point is kept in sync with the first water source that has coordinates, so regeneration, hydrology, elevation and KML export continue to work unchanged.

### Changed

- The settings route now builds one `$set` update (spec + optional location + optional water source Point) instead of branching per field.

## 0.3.23 - 2026-10-08

### Changed

- **AI Generate Sectors** now opens a parameter modal instead of regenerating immediately. Inputs: number of sectors, min/max sector area (pre-filled from project rules), edge buffer (inward boundary inset in meters), EN/AR name prefixes, water-source mode (project water source / custom lat-lng point / ignore), elevation-data toggle (Open-Meteo), and optimization priority (balanced / water proximity / elevation uniformity / area balance / compactness). The chosen parameters are stored on each generated sector as `ai_config`.
- The regeneration engine (`regenerate_sectors`) accepts a `config` dict for all the above; scoring weights are now selectable presets (all normalized to 1.0). Min/max sector area soft-adjusts the sector count so each sector plausibly fits the bounds.

### Fixed

- AI regeneration no longer wipes existing sectors/zones/network/trees before checking that generation can proceed: the pre-clear in the route was removed (the engine already clears internally only after a valid partition is found), and a failure now flashes an error instead of a false success.

## 0.3.22 - 2026-10-08

### Added

- **Estimation tab** in the project workflow (after Hydrology 2): a whole-project quote page that auto-computes quantities from live project data — pipe and dripline lengths per diameter (m), valve and fitting counts by type, and tree count — plus KPI cards for sectors, total area, pipe length and trees. Unit prices are editable and persisted per project in `spec.estimation`, with additional custom line items, notes, currency, discount % and VAT %; subtotals and grand total recalculate live and the page is print-friendly.
- **Land Documents**: the Edit Project modal now supports multiple land/owner/other document records (name, category, reference, notes, optional file upload). Files are stored in the uploads folder and served through an authenticated download route; the project page's Land Documents card lists them with category badges and download buttons. The settings form now posts as `multipart/form-data`.

### Removed

- Min Sectors / Max Sectors fields from the project Rules card, the Edit Project modal and the create-project form; the sector-count rule is no longer part of the project spec.

## 0.3.21 - 2026-10-08

### Changed

- Project page redesign: the separate Info / Progress / Owner / Configuration / Rules / Tree Plan cards are reorganized into a single **Project** container with bilingual split headers (English — icon — Arabic) and trilingual table rows (EN label / value / AR label).
- New placeholder **Land Documents / وثائق الأراضي** card on the project page (empty table, ready for future content).
- Edit Project modal header is now a centered bilingual title bar with the close button in the middle; configuration and rules inputs use a compact 4-column grid.
- Replaced the global `.modal` padding-bottom hack and `.project-settings-modal` rules with scoped `#editSpecModal` CSS: the dialog is capped at `calc(100dvh - 120px)`, `modal-content` is a flex column so header/footer stick while the body scrolls, and a bottom margin keeps the footer above the app's fixed footer.

### Fixed

- Edit Project modal no longer silently resets **Min/Max Sectors** rules to defaults on save: the Min Sectors and Max Sectors inputs were accidentally dropped from the modal and have been restored.
- Rules card again shows the **Zones / Sector** range, which had been lost in the redesign.

## 0.3.20 - 2026-10-08

### Fixed

- Modal footer buttons still inaccessible: previous fix used `max-height` on `.modal-dialog-scrollable` which Bootstrap 5 ignores (the class uses `height: calc(100% - margin*2)` on the dialog element). Replaced with `padding-bottom: 52px !important` on `.modal` itself — this correctly shrinks Bootstrap's internal scroll area upward, keeping the modal-footer buttons always visible above the app's fixed footer.

## 0.3.19 - 2026-10-08

### Fixed

- Edit Project modal footer buttons (Cancel / Save) were hidden behind the app's fixed footer and the modal body was not scrollable: removed the conflicting `modal-dialog-centered` class and added CSS rules to cap the scrollable modal height accounting for the sticky navbar and fixed footer, raise the modal/backdrop `z-index` above the footer, and explicitly enable `overflow-y: auto` on the modal body.

## 0.3.18 - 2026-10-08

### Fixed

- Edit Project modal (`#editSpecModal`) was never shown when the "Edit / تعديل" button was clicked: the `modal-dialog` div was self-closed on the same line, placing `modal-content` outside the Bootstrap modal structure. Corrected the HTML nesting so the modal opens correctly.

## 0.3.17 - 2026-10-07

### Added

- Project information/specification cards on the project page: **Owner** (name/e-mail/phone), **Configuration / Layout** (zones per sector, row/tree spacing, emitter flow, emitters per tree), **Rules** (min/max sectors, sector area range, zones-per-sector range) and **Tree Plan** (variety, AR name, percentage, scope per zone/sector/project).
- "Edit" modal on the project page to update these settings (dynamic tree-plan rows), and an optional "Project Settings" section on the create-project page. Settings are stored per project in `project.spec` and re-merge onto safe defaults when loading.

## 0.3.16 - 2026-10-07

### Changed

- Enforced the app's default fonts application-wide so no other font appears: English text uses **Comfortaa**, Arabic text uses **VIP-Rawy** (each falls back to the other). The Bootswatch theme's fonts are overridden through `--bs-body-font-family`/`--bs-font-sans-serif` and `!important` rules across all text elements.

### Fixed

- Corrected the 0.3.15 typography pass: the bundled `VIP-Rawy` and `Comfortaa` fonts were wrongly removed; they are restored as the defaults and their `preload` links are back in `base.html`.

## 0.3.15 - 2026-10-07

### Changed
Auto Generate
- Replaced the Bootswatch theme font with the app's default fonts (Comfortaa for English, VIP-Rawy for Arabic) across the whole app.

## 0.3.14 - 2026-10-07

### Added

- Geometry - Sectors: "Remove Sector" button (next to "Add Sector") that removes the selected sectors and their related zones, tree rows, trees, and network elements, with a confirmation prompt.

### Fixed

- Zones page no longer raises `'zone' is undefined` (500) after "AI Generate Zones"; removed the leftover per-row AI button that referenced an undefined variable.
- Global loading spinner now stays visible during navigation (animated GIF now plays) instead of being hidden immediately by `beforeunload`/`pagehide`.

## 0.3.13 - 2026-10-07

### Fixed

- Geometry - Zones: the "Validate All" button is now disabled when there are no zones.

## 0.3.12 - 2026-10-07

### Added

- Version badge in the footer (right side), sourced from `__version__` via an app context processor.

## 0.3.11 - 2026-10-07

### Removed

- Dead per-sector zone regeneration endpoint (`/sectors/<id>/zones/ai_regenerate`) and its placeholder helper `regenerate_zones_for_sector`, which imported a non-existent `irrigation.db` / ORM models and would have failed at runtime.

## 0.3.10 - 2026-10-07

### Fixed

- AI regeneration no longer crashes with `unproject_shape() takes 2 positional arguments but 3 were given`; `unproject_shape` now accepts both `(shape, utm_crs)` and `(shape, lng, lat)`.
- Updated all `project_geometry` call sites (regeneration, elevation, validation) to the `(shapely_geometry, crs)` return contract instead of a GeoJSON dict.

## 0.3.9 - 2026-10-07

### Changed

- Loading spinner now appears only after the confirmation modal is accepted for confirm-protected actions, and immediately for all other navigation and form submissions.

## 0.3.8 - 2026-10-07

### Added

- Global centered animated loading overlay on every navigation and form submission.

### Fixed

- Projection utilities now accept 3D coordinates and `boundary`/`polygon` geometry fields.
- `projected_shape` returns a shapely shape by default and supports `return_crs=True`.

## 0.3.7 - 2026-10-05

### Changed

- Replace native browser confirmation dialogs with a shared Bootstrap confirmation modal.

## 0.3.6 - 2026-10-05

### Added

- Map selection mode for Geometry sectors and zones, synchronized with table checkboxes.

## 0.3.5 - 2026-10-05

### Changed

- Apply the default English and Arabic fonts to Geometry map titles, controls, legends, and popups.

## 0.3.4 - 2026-10-05

### Changed

- Place Merge and Swap beside Add Sector in the Geometry action row.

## 0.3.3 - 2026-10-05

### Changed

- Center the Geometry Sectors and Zones tabs as a group.

## 0.3.2 - 2026-10-05

### Changed

- Split the Geometry Sectors title into English, centered icon, and Arabic columns with explicit fonts.

## 0.3.1 - 2026-10-05

### Changed

- Remove the redundant Back buttons from the Geometry Sectors and Zones pages.

## 0.3.0 - 2026-10-05

### Added

- In-map Geometry editor controls for polygons, lines, markers, and removing draft shapes.
- Polygon drawings open the existing Sector or Zone form with coordinates filled in.

## 0.2.0 - 2026-10-05

### Added

- Bilingual project workflow tabs with section-specific navigation.
- Login and registration forms in switchable tabs.
- Top-right flash toasts that dismiss after five seconds.
- Local English and Arabic fonts, including Arabic-safe numeric display.
- KML map overlays and sector import support.

### Changed

- Reworked bilingual headings, form labels, buttons, and navigation layouts.
- Added a centered Create Project action to the Projects page.

### Fixed

- Updated KML parsing for supported FastKML APIs.
- Select the largest KML polygon as the field boundary.
- Include imported KML overlays in the map API and show imported sectors in project progress.
- Remove the fixed fallback Flask session key.
