# OPENCODE.md - Web App Rebuild Prompt

## Project Overview
Irrigation Planning System - A Flask-based web application for irrigation planning and management.

## Architecture Overview

### Main Tab Navigation Structure
The main navigation tabs follow this order:
```
Project - Water Resource - Geometry - Hydrology1 - Trees - Hydrology2 - Estimation - Report
```

### Sub-Tab Structure

#### 1. Geometry
- **Sectors**: Sector management (add, edit, rename, remove, split, merge, swap)
- **Zones**: Zone management (add, edit, rename, remove, split, merge, swap)

#### 2. Water Resource
- **Well**: Well management
- **Basin**: Basin management
- **Other**: Other water sources

#### 2. Hydrology1
- **Main Pipe (MP)**: Main pipe management (add, edit, rename, remove, AI generation)
- **Secondary Pipe (SP)**: Secondary pipe management
- **Zone Pipe (ZP)**: Zone pipe management

#### 2. Trees
- **Rows**: Row management (add, edit, rename, remove, generate)
- **Trees**: Tree management (add, edit, rename, remove, generate)

#### 3. Hydrology2
- **dripline**: Dripline management
- **Reduction**: Reduction management
- **Pression Reduction**: Pressure reduction management
- **Air Elimiator**: Air eliminator management
- **Connectors to dripline**: Connectors management

#### 4. Estimation
- **Configuration**: Estimation configuration (prices, items, notes, discount, tax)
- **Result (Calculation)**: Estimation results and calculations

#### 4. Report
- **Charts**: Charts and graphs
- **Bilan Hydrique**: Bilan hydrique report
- **Technical Report (model for DPA)**: Technical report model for DPA

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
- **MongoDB**: Document database
- **Collections**:  Irrigation: lands>project>sectors>zones>rows>trees,  customers, water_resources, estimation, report ......
### Profile
- Full Admin
- Admin (Entrepreneur)
- Customer

### Key Features
- Bilingual interface (English/Arabic)
- KML/KMZ import/export
- AI-powered sector/zones generation
- Hydraulic calculations and validation
- Estimation and cost calculation
- Report generation (charts, bilan hydrique, technical reports)

## Rebuild Instructions

### Prerequisites
1. Python 3.9+
2. MongoDB running
3. pip install -r requirements.txt


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
6. **irrigation/trees/routes.py**: Trees management routes
7. **irrigation/estimation/routes.py**: Estimation routes

### Navigation Structure
The main navigation should follow this order:
```
Project - Water Resource - Geometry - Hydrology1 - Trees - Hydrology2 - Estimation - Report
```

With sub-tabs:
- **Geometry**: Sectors, Zones
- **Water Resource**: Well, Basin, Other
- **Hydrology1**: Main Pipe(MP) - Secondary Pipe(SP) - Zone Pipe(ZP)
- **Trees**: Rows, Trees
- **Hydrology2**: dripline-goutteur-Reduction-Pression Reduction-Air Elimiator-Connectors to dripline-etc....
- **Estimation**: Configuration - Result(Calculation)
- **Report**: Charts-Standard-Bilan Hydrique-Technical Report (model for DPA)

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
- github/kouljihate/FIS.git
- Changelog: CHANGELOG.md

### Contributing
- Follow existing code patterns
- Maintain bilingual (English/Arabic) support
- Ensure responsive design
- Maintain API consistency
