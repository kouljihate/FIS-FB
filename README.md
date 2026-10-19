# FIS (Farming Irrigation System)

Version: **0.3.80**

FIS is a Flask irrigation-planning application for organizing farm projects, field geometry, irrigation networks, trees, and engineering validation.

## Features

- Workflow navigation organized into eight tabs: Project, Water Resource, Geometry, Pipes, Trees, Components, Estimation, and Report.
- Water Resource section with Well, Basin, and Other sub-tabs.
- Manage projects, boundaries, sectors, zones, rows, and trees.
- Import KML/KMZ geometry, including field boundaries, overlay features and water-source points.
- AI-generate sectors (with an optional elevation-aware contour-band strategy) and zones.
- AI-generate the full piping network (main, zone and lateral pipes plus driptubes) with hydraulic sizing, pressure/elevation design and a KPI report.
- Maintain multiple water sources (well, basin, reservoir, pump, canal) per project.
- Draw polygons, lines, and markers with the Geometry map editor.
- Select and multi-select sectors/zones from the map and sync them with table actions.
- Generate and edit tree rows per zone from the dedicated Rows workflow tab.
- Confirm destructive or major actions with a shared Bootstrap modal instead of browser dialogs.
- View project geometry and irrigation elements on a Leaflet map.
- Configure main and sub-pipes, valves, driplines, and network components.
- Auto-compute a whole-project cost estimate with editable unit prices.
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
$env:MONGO_URI = 'mongodb://localhost:27017/irrigation_db'
$env:SECRET_KEY = 'set-a-unique-random-secret-before-use'
python app.py
```

The application is served at `http://127.0.0.1:5000`. If `SECRET_KEY` is unset, a random key is generated for local development and sessions reset when the process restarts. Set a unique, persistent `SECRET_KEY` and configure MongoDB appropriately before deployment.

## Release notes

See [CHANGELOG.md](CHANGELOG.md).