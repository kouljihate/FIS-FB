# FIS (Farming Irrigation System)

Version: **0.4.53**

FIS is a Flask irrigation-planning application for organizing farm projects, field geometry, irrigation networks, trees, and engineering validation.

## Features

- Workflow navigation organized into eight tabs: Project, Water Resource, Geometry, Pipes, Trees, Components, Estimation, and Report.
- Water Resource section with Well, Basin, and Other sub-tabs, built with the Sectors layout: each tab lists the project's sources of its kind with add, edit, rename, remove and validate actions over a page map.
- Manage projects, boundaries, sectors, zones, rows, and trees.
- Import KML/KMZ geometry, including field boundaries, overlay features and water sources; a basin or reservoir drawn as a KML polygon is listed as a source at its centroid.
- AI-generate sectors (with an optional elevation-aware contour-band strategy) and zones.
- AI-generate the full piping network (main, zone and lateral pipes plus driptubes) with hydraulic sizing, pressure/elevation design and a KPI report.
- Maintain multiple water sources (well, basin, reservoir, pump, canal) per project.
- Draw polygons, lines, and markers with the Geometry map editor.
- Select and multi-select sectors/zones from the map and sync them with table actions.
- Generate and edit tree rows per zone from the dedicated Rows workflow tab.
- Confirm destructive or major actions with a shared Bootstrap modal instead of browser dialogs.
- Project map showing only the sector layout, the water sources (one marker per well, basin, reservoir, pump or canal) and an elevation heatmap with its own toggle and gradient legend.
- View project geometry and irrigation elements on a Leaflet map.
- Configure main and sub-pipes, valves, driplines, and network components.
- Auto-compute a whole-project cost estimate with editable unit prices, then review the 5-year return on investment (ROI %, payback, yearly cash flow) with assumptions saved per project.
- Attach land/owner/other documents to a project.
- Run engineering validation and review elevation-aware reports.
- Forgotten-password flow on the login page: a single-use temporary password is e-mailed and sent over WhatsApp, a new password is forced at first sign-in, and an unused temporary password that is requested again disables the account until an administrator re-enables it.
- Send a direct WhatsApp message to a project's Project Admin or Owner from the project page.
- Use the bilingual English/Arabic interface.

## Requirements

- Python 3.10 or newer
- MongoDB

## Run locally

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:MONGO_URI = 'mongodb://localhost:27017/FIS'
$env:SECRET_KEY = 'set-a-unique-random-secret-before-use'
python app.py
```

The application is served at `http://127.0.0.1:5000`. If `SECRET_KEY` is unset, a random key is generated for local development and sessions reset when the process restarts. Set a unique, persistent `SECRET_KEY` and configure MongoDB appropriately before deployment.

### E-mail (temporary passwords)

The Forget Password tab sends a one-time password over SMTP. Use any mail account; set these before starting the app:

```powershell
$env:SMTP_HOST = 'smtp.gmail.com'
$env:SMTP_PORT = '587'
$env:SMTP_USER = 'you@example.com'
$env:SMTP_PASSWORD = 'app-password'
$env:SMTP_FROM = 'you@example.com'   # defaults to SMTP_USER
$env:SMTP_USE_TLS = '0'              # only for a relay without STARTTLS
```

Without SMTP settings the message, temporary password included, is written to `logs/irrigation.log`, which is enough to exercise the flow locally.

### WhatsApp (Blueticks)

The same one-time password also goes to the phone number saved on the account, and the project page has a WhatsApp composer that messages the Project Admin or the Owner. Messages are sent through the Blueticks REST API (https://blueticks.co) from the WhatsApp number linked to that account.

Create a key at https://dev.blueticks.co, then either set it in the environment:

```powershell
$env:BLUETICKS_API_KEY = 'bt_live_...'
$env:WHATSAPP_DEFAULT_COUNTRY_CODE = '216'   # prefix for local numbers
$env:WHATSAPP_TIMEOUT = '15'                 # seconds
```

or keep it in a `.env` file at the project root, which the application loads on startup and git ignores:

```
BLUETICKS_API_KEY=bt_live_...
WHATSAPP_DEFAULT_COUNTRY_CODE=216
```

Phone numbers can be written with a country prefix (`+216 ...`, `00216 ...`) or locally (`12345678`); the local form gets `WHATSAPP_DEFAULT_COUNTRY_CODE`. The Project Admin's number comes from that user's profile, the Owner's from the project settings. Without `BLUETICKS_API_KEY` the message is written to `logs/irrigation.log` instead of being sent, so the flow still works locally.

## Database

The app uses the **`FIS`** database with exactly two collections, `user` and `project`. Project geometry, pipes, components and reports are embedded in the project document. The schema, validators and indexes are created automatically at startup; the first registered account becomes the `Admin`. See [DATABASE.md](DATABASE.md) for the full model and role rules.

## Release notes

See [CHANGELOG.md](CHANGELOG.md).