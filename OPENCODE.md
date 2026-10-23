# OPENCODE.md - Web App Rebuild Prompt

## Project Overview
Irrigation Planning System - A Flask-based web application for irrigation planning and management.

## Current Version
- **Version**: 0.4.24
- **Last Updated**: 2026-10-23

## Architecture Overview

### Main Tab Navigation Structure
The main navigation tabs follow this order:
```
Project - Water Resource - Geometry - Pipes - Trees - Components - Estimation - Report
```

### Sub-Tab Structure

#### 1. Project
- **New**: Create a new project
- **Load**: Load an existing project
- **Link**: Open the project workspace (land documents, water sources, KML link/upload) and its map: sector outlines, water sources and the elevation heatmap only

#### 2. Water Resource
- **Well**: Well management (Coming Soon placeholder)
- **Basin**: Basin management (Coming Soon placeholder)
- **Other**: Other water sources (Coming Soon placeholder)

#### 3. Geometry
- **Sectors**: Sector management (add, edit, rename, remove, split, merge, swap)
- **Zones**: Zone management (add, edit, rename, remove, split, merge, swap)

#### 4. Pipes
- **Main Pipe (MP)**: Main pipe management (add, edit, rename, remove, Auto generation)
- **Second Pipe (SP)**: Secondary pipe management
- **Zone Pipe (ZP)**: Zone pipe management

#### 5. Trees
- **Rows**: Row management (add, edit, rename, remove, generate)
- **Trees**: Tree management (add, edit, rename, remove, generate)

#### 6. Components
- **Valves**: Master, sector and zone valves
- **Connectors**: Connectors to dripline (Coming Soon placeholder)
- **Reductors**: Diameter reductions (Coming Soon placeholder)
- **Air Removal**: Air release points (Coming Soon placeholder)
- **Reduce Pressure**: Pressure reducers (Coming Soon placeholder)
- **Terminators**: Line terminators (Coming Soon placeholder)

#### 7. Estimation
- **Pricing**: Estimation pricing configuration (prices, items, notes, discount, tax)
- **ROI**: Return on investment after 5 years of exploitation (investment from the priced quote, yearly revenue and costs, yearly cash flow, ROI %, payback; assumptions saved per project)
- **Result**: Estimation results and calculations (Coming Soon placeholder)

#### 8. Report
- **Charts**: Charts and graphs (Coming Soon placeholder)
- **Standard**: Engineering validation report
- **Technical (DPA)**: Technical report model for DPA

## Technical Details

### Framework
- **Flask**: Python web framework
- **Flask-Login**: Authentication management
- **Flask-WTF**: Form handling
- **Flask-Login**: User session management

### Key Components
- **irrigation/**: Main Python package
- **irrigation/templates/**: HTML templates
- **irrigation/static/**: Static files (CSS, JS, images)
- **irrigation/api/**: API routes
- **irrigation/engineering/**: Engineering calculations
- **irrigation/hydrology**: Hydrology functions
- **irrigation/geometry**: Geometry operations
- **irrigation/trees**: Tree management
- **irrigation/estimation**: Estimation calculations

### Database
- **MongoDB**: Document database `FIS`
- **Collections**: `user` and `project` only
- **Model**: land/water/sector/zone/row/tree/pipes/components/reports are embedded in each project document; see `DATABASE.md`
- **Roles**: Admin, Project Admin, Project Owner (enforced in `irrigation/access.py`)

### Key Features
- Bilingual interface (English/Arabic)
- KML/KMZ import/export
- AI-powered sector/zones generation
- Hydraulic calculations and validation
- Estimation, cost calculation and 5-year ROI
- Report generation (charts, bilan hydrique, technical reports)

## Rebuild Instructions

### Prerequisites
1. Python 3.9+
2. MongoDB running
3. pip install -r requirements.txt

### Setup
```bash
cd irrigation
pip install -e .
```

### Running the App
```bash
python -c "from irrigation import create_app; app = create_app(); app.run(host='0.0.0.0', port=5000, debug=True)"
```

### Template Structure
- **base.html**: Main layout with navigation
- **auth/**: Authentication templates
- **project/**: Project management templates
- **geometry/**: Geometry management templates
- **hydrology/**: Hydrology management templates
- **trees/**: Tree management templates
- **estimation/**: Estimation templates
- **engineering/**: Engineering templates

### Key Files to Modify
1. **irrigation/templates/base.html**: Main navigation structure
2. **irrigation/auth/routes.py**: Authentication routes
3. **irrigation/project/routes.py**: Project management routes
4. **irrigation/geometry/routes.py**: Geometry management routes
5. **irrigation/hydrology/routes.py**: Hydrology management routes
6. **irrigation/trees/routes.py**: Tree management routes
7. **irrigation/estimation/routes.py**: Estimation routes

### Navigation Structure
The main navigation should follow this order:
```
Project - Water Resource - Geometry - Pipes - Trees - Components - Estimation - Report
```

With sub-tabs:
- **Project**: New - Load - Link
- **Water Resource**: Well - Basin - Other
- **Geometry**: Sectors - Zones
- **Pipes**: Main Pipe (MP) - Second Pipe (SP) - Zone Pipe (ZP)
- **Trees**: Rows - Trees
- **Components**: Valves - Connectors - Reductors - Air Removal - Reduce Pressure - Terminators
- **Estimation**: Pricing - ROI - Result (Coming Soon)
- **Report**: Charts (Coming Soon) - Standard - Technical (DPA)

### API Endpoints
Key API endpoints for rebuild:
- `/api/project/<id>/geometry`: Get geometry data
- `/water_resource/...`: Water resource routes
- `/hydrology/...`: Hydrology routes
- `/trees/...`: Tree routes
- `/estimation/...`: Estimation routes

### Testing
- Run existing tests: `pytest`
- Check all routes are functional
- Verify navigation works correctly

### Deployment
- Set up MongoDB connection
- Configure environment variables
- Run: `python app.py`
- Access at: `http://localhost:5000`

### Version Control
- Git branches: `project-settings-v1`
- Version: 0.4.24
- Changelog: CHANGELOG.md
- **ALWAYS bump the version on every change (never forget).** Increment `__version__` in
  `irrigation/__init__.py` (patch `+1`, e.g. `0.3.44` -> `0.3.45`), add a dated `## <version> - YYYY-MM-DD`
  entry at the top of `CHANGELOG.md`, and sync the version in this file (Current Version + Version Control)
  and `README.md`.

### Release
- Run `python release.py --summary "..." --note "Added: ..." --yes` after **every** change prompt:
  it bumps the version, writes the dated `CHANGELOG.md` entry, syncs the version in this file and
  `README.md`, commits as `vX.Y.Z: <summary>`, and pushes the branch to the `fb` remote
  (`git@github.com:kouljihate/FIS-FB.git`), branch `main`.
- `python release.py --push-only` retries just the push (after fixing credentials); the script falls
  back to HTTPS when the SSH key is denied.
- `python release.py --list` prints every recorded version and the pending changes without touching
  anything; `--dry-run` prints the plan only; `--no-push` commits locally without pushing.
- Note format: `--note "Added|Changed|Fixed: text"`; notes without a section land under `Changed`.
### Contributing
- Follow existing code patterns
- Maintain bilingual (English/Arabic) support
- Ensure responsive design
- Maintain API consistency

---
*Generated for Irrigation Planning System rebuild*