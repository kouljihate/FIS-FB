# Review — github.com/karimouljihate/FIS

Reviewed 2026-10-18 by cloning the repository fresh and probing it against a live
MongoDB. Nothing in this workspace's application code was changed; the artifacts
next to this report are the review's evidence and the prepared fixes.

| artifact | what it is |
|---|---|
| `fis-github-fixes.patch` | fixes for four confirmed bugs; `git apply`s cleanly to `project-settings-v1` at v0.3.73 and at the newer v0.3.79 |
| `fix-checks.py` | 16 assertions covering the fixes; exits non-zero on failure |
| `smoke-pages.py` | requests all 27 GET routes of the app and reports 5xx |

```bash
git clone https://github.com/karimouljihate/FIS && cd FIS
git checkout project-settings-v1
git apply /path/to/fis-github-fixes.patch
MONGO_URI=mongodb://127.0.0.1:27017/fis_check python /path/to/fix-checks.py . fis_check
```

## 1. What the repository actually contains

| branch | commits | version | size | last commit |
|---|---|---|---|---|
| `main` (default) | 12 | 0.3.7 | 61 files, 4,754 Python lines | 2026-10-06 |
| `fix/phase1-4` | 23 | 0.3.17 | — | 2026-10-07 |
| `project-settings-v1` | **85** | **0.3.79** | **78 files, 9,097 Python lines** | **2026-10-19** |

`project-settings-v1` is 73 commits ahead of `main` and is still being committed
to: it moved from v0.3.73 to v0.3.79 (five commits, touching only `CHANGELOG.md`,
`base.html`, `trees/trees.html`, `hydrology/routes.py` and a new
`hydrology2/placeholder.html`) while this report was being written. The patch was
re-applied and the 16 assertions re-run on that new head — all pass. Anyone who clones the repository gets `main`, i.e. version 0.3.7 from
twelve days earlier. **Every finding below was re-verified on both branches** —
none of them is fixed on the active one.

This repository is not an ancestor of the workspace application. Both implement
the same `OPENCODE.md` brief in parallel: this one uses integer `id`s from a
`counters` collection and keeps its engineering logic in `irrigation/engineering/`,
while the workspace application uses ObjectIds, has a different module layout and
a test suite. Treat the repository as a source of engineering logic, not as
upstream.

## 2. Bugs reproduced, with evidence

All four were executed against the real code paths with Flask test clients and a
throwaway MongoDB database (`fis_review_*`, `fis_probe_*`, dropped afterwards).

### 2.1 Any logged-in user can wipe another user's project data — `project/routes.py:557` (psv1) / `:233` (main)

```python
mongo.db.projects.delete_one({'id': project_id, 'user_id': int(current_user.id)})
mongo.db.sectors.delete_many({'project_id': project_id})   # no ownership filter
```

The scoped `delete_one` matches nothing for a foreign project, but the five
unscoped `delete_many` calls run anyway. Measured: user *mallory* POSTs
`/<alice_id>/delete`, receives `Project deleted / تم حذف المشروع`, and Alice's
project row survives while **all** of its sectors, zones, network elements, tree
rows and trees are destroyed (`alice sectors left: 0`). Read paths are correctly
scoped — Bob gets `Project not found` and a `404` from the geometry API — so this
handler is the lone outlier. It is also the part `fis-split-selected.patch` on
`main` did *not* cover; that patch scoped the same deletes in `geometry/routes.py`.

*Fixed in the patch.*

### 2.2 KML export has never worked — two separate faults

1. `utils/kml.py:63` fetched the project with `find_one({'_id': project_id})`
   where `project_id` is the integer `id` and `_id` is an ObjectId, so it always
   returned `None` and the route reported `No geometry to export`.
2. Behind that, the builder used fastkml's old positional constructors
   (`kml.Document(ns, id, name, desc)`), set geometry through `Placemark.geometry`
   and serialised with `to_element()`. In fastkml 1.x the second positional
   parameter is the namespace map, `Placemark.geometry` has no setter and
   `to_element` does not exist — so fixing the lookup alone produces
   `TypeError: 'str' object is not a mapping`. Verified failing identically under
   **fastkml 1.0.0 (the pinned version) and 1.4.0**.

*Both fixed in the patch; the export route now returns 200 with a valid
`application/vnd.google-earth.kml+xml` file carrying the land boundary.*

### 2.3 A blank email locks out every later registration — `auth/routes.py:21`

`find_one({'$or': [{'username': username}, {'email': email}]})` with an optional
email: the first account is stored as `email: ''`, so every subsequent blank-email
signup is rejected as a duplicate. Measured on both branches: `alice` created,
`bob` → `User already exists`, stored users `['alice']`.

*Fixed in the patch — the email clause is only added when an email is supplied,
so duplicate usernames and duplicate real emails are still refused (asserted).*

### 2.4 Malformed uploads raise instead of reporting — `project/routes.py:493`

* a `.kml` that is not XML → `ValueError` out of lxml
* a `.kmz` that is not a zip → `BadZipFile`
* a 55 KB `.kmz` expanding to ~20 MB of Placemarks → `DocumentTooLarge` from
  MongoDB, because the parsed features are written into the project document
* any extension is accepted and stored (a `.html` upload landed in `uploads/`)

With `debug=True` in `app.py` each of these is a traceback page.

*Fixed in the patch: extension allow-list, `try/except` around parsing with a
bilingual flash and a logged warning, an 8 MB decompressed-KML ceiling inside
`parse_kmz_file`, and `InvalidDocument` handling around the store call.*

## 3. Additional pre-existing faults the smoke pass found

Both reproduce on the **pristine** `project-settings-v1` tree, are unrelated to the
fixes above, and are not fixed by the patch:

| route | cause |
|---|---|
| `/estimation/<id>` → 500 | `templates/estimation/quote.html:119` — `{% for item in estimation.items %}` resolves `items` to the dict method: `TypeError: 'builtin_function_or_method' object is not iterable` |
| `/engineering/<id>/technical-report` → 500 | `templates/engineering/technical_report.html:1` is `{% extends base.html %}` (no quotes) → `UndefinedError: 'base' is undefined` |

## 4. Verified clean (do not re-investigate)

* **XXE through KML** — an external entity (`file:///…`) was not resolved: the
  feature name stored empty and the target file's contents never reached the page.
  Measured with lxml 6.1.3; the repo pins lxml 5.1.0, so re-test if you upgrade.
* **`get_next_id`** — `return_document=True` is correct on pymongo 4
  (`ReturnDocument.AFTER is True`; confirmed in the installed 4.16 source), so
  the counter does not hand out duplicate ids. A plausible-looking bug that is not one.
* **Route protection** — 89 routes on `project-settings-v1`; only `/login` and
  `/register` lack `@login_required`.
* **Read-path tenancy** — project detail and the geometry API both scope by
  `user_id` and correctly 404/redirect for a foreign project.

## 5. Repo hygiene

* `main` carries `irrigation.zip` — a 440 KB snapshot holding 114 files, more than
  `main`'s own 61 tracked files, i.e. a stale duplicate of the project committed
  into the repository, free to diverge from the code.
* `main` carries `fis-split-selected.patch` — a 50 KB diff that reverse-applies
  cleanly, i.e. work that is already committed, committed twice.
* Five binary fonts tracked; CRLF line endings throughout; no `LICENSE`, no CI
  configuration, and **no tests in any branch**.
* `requirements.txt` pins `fastkml==1.0.0`, `lxml==5.1.0`, `numpy==1.26.3`, but
  leaves `Flask-PyMongo` and `shapely` unpinned.
* `app.py` runs `debug=True` (port 5000 on `main`, 5001 on `project-settings-v1`).
* `SECRET_KEY` falls back to `os.urandom(32)`, so sessions silently reset on
  restart and break under multiple workers. The README documents this; a hard
  failure outside development would be safer.
* No CSRF protection anywhere (flask-wtf is not a dependency); `logout` is a GET
  link; no password policy (`'a'` is accepted) and no login rate limiting.
* Branch history has two commits titled "Initial commit" and a `main` head whose
  message is just `2610061456`.

## 6. Structure and quality

The engineering core is the reason to keep this repository. `engineering/piping.py`
(1,547 lines) routes the main pipe as a minimum-spanning tree over the water source
and sector centroids, taps zone pipes at nearest points, sizes each PVC segment
against a velocity limit, carries head loss with terrain elevation through
pyproj/UTM, places valves and diameter reductions, and writes a bilingual report.
`validation.py` returns dataclass checks with severities and persists a validation
history; `hydraulics.py` documents its defaults in a SI-unit table. None of that
exists in the workspace application.

Around that core: `get_project_or_redirect` is copy-pasted into **six** blueprints
(52 call sites); `sector_split`/`zone_split`, `sector_split_selected`/
`zone_split_selected`, `sector_validate`/`zone_validate` and
`main_pipe_add`/`second_pipe_add` are byte-identical twins after normalisation
(5 duplicate-body groups); twelve functions exceed 80 lines, the largest 388
(`piping.py:354`). There are zero `TODO`s and zero bare `except:`, but also zero
tests, so 9,000 lines — half of it numeric — has no regression net. Bilingual text
is built by concatenating `'English / Arabic'` inline in Python and templates
rather than through a string table.

## 7. Recommended order of work

1. Apply `fis-github-fixes.patch` (four bugs, verified).
2. Fix the two template faults in §3 — both are one-line changes.
3. Add CSRF (Flask-WTF), make `logout` a POST, and add a minimum password length.
4. Delete `irrigation.zip` and `fis-split-selected.patch`; add a `LICENSE` and CI.
5. Fast-forward or delete `main` and `fix/phase1-4` so clones do not start at 0.3.7.
6. Start a test suite around `engineering/` — the hydraulics and piping maths is
   the part that cannot be reviewed by reading.

## 8. Limits of this review

* The branch head is a moving target. Findings and fixes were verified at
  `ec5b9c9` (v0.3.73) and re-verified after `git apply` at `78221a7` (v0.3.79);
  re-run `fix-checks.py` if the head moves again, especially if
  `auth/routes.py`, `project/routes.py` or `utils/kml.py` change.
* Probes ran with `fastkml` 1.0.0 and 1.4.0, `lxml` 6.1.3, `pymongo` 4.16,
  `shapely` 2.1.2 — not the exact pinned set for every package.
* No browser was used: template rendering is covered only through the smoke pass'
  status codes, not visually.
* No concurrency, load, or multi-worker session testing.
* MongoDB behaviour was exercised through a local single-node instance only.
