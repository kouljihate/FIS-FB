# FIS (Farming Irrigation System)

Version: **0.4.15**

FIS is a Flask irrigation-planning application for organizing farm projects, field geometry, irrigation networks, trees, and engineering validation.

## Features

- Workflow navigation organized into eight tabs: Project, Water Resource, Geometry, Pipes, Trees, Components, Estimation, and Report.
- Water Resource section with Well, Basin, and Other sub-tabs.
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

## Database

The app uses the **`FIS`** database with exactly two collections, `user` and `project`. Project geometry, pipes, components and reports are embedded in the project document. The schema, validators and indexes are created automatically at startup; the first registered account becomes the `Admin`. See [DATABASE.md](DATABASE.md) for the full model and role rules.

## Release notes

See [CHANGELOG.md](CHANGELOG.md).