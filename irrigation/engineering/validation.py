"""Engineering validation engine.

Comprehensive validation combining:
- Hydraulic calculations (flow, velocity, pressure loss, pipe sizing)
- Network connectivity (graph-based validation)
- Geometric containment (sectors in boundary, zones in sectors, etc.)
- Coverage checks (valves per sector/zone, drips per tree)
- Water source capacity

Produces structured validation results with pass/warning/fail severity,
bilingual messages, and actionable recommendations.
"""
import math
from datetime import datetime
from dataclasses import asdict
from shapely.geometry import shape as shp_shape, Point, Polygon, box
from shapely.ops import unary_union

from irrigation.extensions import mongo
from irrigation.engineering.projection import (
    projected_shape, get_project_centroid, project_geometry
)
from irrigation.engineering.elevation import (
    get_or_build_elevation_model, elevation_metrics_for_polygon,
    get_elevation_at_point, build_elevation_heatmap_geojson
)
from irrigation.engineering.hydraulics import (
    ENGINEERING_DEFAULTS, HydraulicCheck,
    zone_flow_demand_m3s, sector_flow_demand_m3s,
    pipe_velocity_mps, hazen_williams_head_loss_m,
    distributed_friction_loss_m, pressure_bar_from_head_m,
    recommended_diameter_mm, calculate_pipe_length_m,
    validate_pipe_velocity, validate_pressure_loss,
    validate_dripline, validate_water_source_capacity,
    flow_per_tree_lph
)


def _line_end_coords(geom):
    """Return the first and last coordinates of a line-like geometry.

    Handles both ``LineString`` and the ``MultiLineString`` used for the
    merged AI main pipe (returns the outermost coordinates).
    """
    if geom is None or geom.is_empty:
        return None, None
    if geom.geom_type == 'LineString':
        coords = list(geom.coords)
        return (coords[0], coords[-1]) if coords else (None, None)
    if geom.geom_type == 'MultiLineString':
        parts = [list(g.coords) for g in geom.geoms if not g.is_empty]
        parts = [c for c in parts if c]
        if not parts:
            return None, None
        return parts[0][0], parts[-1][-1]
    return None, None


def run_full_validation(project_id, config=None):
    """Run comprehensive engineering validation for a project.

    Returns a structured validation report with all checks.
    """
    if config is None:
        config = ENGINEERING_DEFAULTS.copy()

    project = mongo.db.projects.find_one({'id': project_id})
    if not project:
        return {'error': 'Project not found'}

    lng, lat = get_project_centroid(project)
    checks = []

    # === 1. GEOMETRIC CONTAINMENT CHECKS ===
    _validate_geometric_containment(checks, project_id, project, lng, lat)

    # === 2. HYDRAULIC VALIDATION ===
    _validate_hydraulics(checks, project_id, project, config, lng, lat)

    # === 3. NETWORK CONNECTIVITY ===
    _validate_network_connectivity(checks, project_id, project, lng, lat)

    # === 4. VALVE COVERAGE ===
    _validate_valve_coverage(checks, project_id)

    # === 5. DRIP LINE COVERAGE ===
    _validate_dripline_coverage(checks, project_id, config, lng, lat)

    # === 6. WATER SOURCE CAPACITY ===
    _validate_water_source(checks, project_id, config)

    # === 7. TREE/ROW COVERAGE ===
    _validate_tree_coverage(checks, project_id, config)

    # Build summary
    passes = sum(1 for c in checks if c.severity == 'pass')
    warnings = sum(1 for c in checks if c.severity == 'warning')
    failures = sum(1 for c in checks if c.severity == 'fail')

    if failures > 0:
        status = 'fail'
    elif warnings > 0:
        status = 'warning'
    else:
        status = 'pass'

    result = {
        'project_id': project_id,
        'created_at': datetime.utcnow(),
        'summary': {
            'status': status,
            'passes': passes,
            'warnings': warnings,
            'failures': failures,
            'total_checks': len(checks)
        },
        'checks': [asdict(c) if hasattr(c, '__dataclass_fields__') else c.__dict__ for c in checks],
        'config': config
    }

    # Save to database
    db_result = dict(result)
    db_result['checks'] = [asdict(c) if hasattr(c, '__dataclass_fields__') else dict(c) for c in checks]
    mongo.db.engineering_validations.insert_one(db_result)
    result['_id'] = str(result.get('_id', ''))

    return result


def _validate_geometric_containment(checks, project_id, project, lng, lat):
    """Validate that geometric elements are properly contained."""
    boundary = project.get('boundary')

    if not boundary:
        checks.append(HydraulicCheck(
            code='BOUNDARY_EXISTS',
            severity='warning',
            object_type='project',
            object_id=project_id,
            message_en='No land boundary defined',
            message_ar='لا توجد حدود أرض محددة',
            recommendation_en='Define a land boundary for proper validation',
            recommendation_ar='حدد حدود الأرض للتحقق المناسب'
        ))
        return

    boundary_shapely = projected_shape(boundary, lng, lat)

    # Check sectors are inside boundary
    for sector in mongo.db.sectors.find({'project_id': project_id}):
        if not sector.get('polygon'):
            continue
        sector_shapely = projected_shape(sector['polygon'], lng, lat)
        if not boundary_shapely.contains(sector_shapely):
            diff = sector_shapely.difference(boundary_shapely)
            overflow_area = diff.area if not diff.is_empty else 0
            severity = 'warning' if overflow_area < sector_shapely.area * 0.05 else 'fail'
            checks.append(HydraulicCheck(
                code='SECTOR_IN_BOUNDARY',
                severity=severity,
                object_type='sector',
                object_id=sector['id'],
                message_en=f"Sector {sector.get('name_en', '')} extends outside land boundary ({overflow_area:.1f} m² overflow)",
                message_ar=f"القطاع {sector.get('name_ar', '')} يمتد خارج حدود الأرض ({overflow_area:.1f} م² تجاوز)",
                metrics={'overflow_area_m2': round(overflow_area, 2)},
                recommendation_en='Adjust sector polygon to stay within land boundary',
                recommendation_ar='اضبط مضلع القطاع للبقاء ضمن حدود الأرض'
            ))
        else:
            checks.append(HydraulicCheck(
                code='SECTOR_IN_BOUNDARY',
                severity='pass',
                object_type='sector',
                object_id=sector['id'],
                message_en=f"Sector {sector.get('name_en', '')} is within boundary",
                message_ar=f"القطاع {sector.get('name_ar', '')} ضمن الحدود"
            ))

    # Check zones are inside their sectors
    for zone in mongo.db.zones.find({'project_id': project_id}):
        if not zone.get('polygon'):
            continue
        zone_shapely = projected_shape(zone['polygon'], lng, lat)
        sector = mongo.db.sectors.find_one({'id': zone.get('sector_id')})
        if sector and sector.get('polygon'):
            sector_shapely = projected_shape(sector['polygon'], lng, lat)
            if not sector_shapely.contains(zone_shapely):
                checks.append(HydraulicCheck(
                    code='ZONE_IN_SECTOR',
                    severity='warning',
                    object_type='zone',
                    object_id=zone['id'],
                    message_en=f"Zone {zone.get('name_en', '')} extends outside its sector",
                    message_ar=f"المنطقة {zone.get('name_ar', '')} تمتد خارج قطاعها",
                    recommendation_en='Adjust zone polygon to fit within its sector',
                    recommendation_ar='اضبط مضلع المنطقة لتناسب قطاعها'
                ))
            else:
                checks.append(HydraulicCheck(
                    code='ZONE_IN_SECTOR',
                    severity='pass',
                    object_type='zone',
                    object_id=zone['id'],
                    message_en=f"Zone {zone.get('name_en', '')} is within its sector",
                    message_ar=f"المنطقة {zone.get('name_ar', '')} ضمن قطاعها"
                ))


def _validate_hydraulics(checks, project_id, project, config, lng, lat):
    """Validate hydraulic parameters for all pipes and drip lines."""
    emitter_flow = config['emitter_flow_lph']
    emitters_per_tree = config['emitters_per_tree']

    # Fetch elevation model for pressure head calculations
    elevation_model = None
    water_source_elev = None
    try:
        elevation_model = get_or_build_elevation_model(project_id, project)
        if elevation_model:
            water_source_elev = elevation_model.get('water_source_elevation_m')
    except Exception:
        pass

    # === Main pipes ===
    main_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'main_pipe'}))
    for pipe in main_pipes:
        # Main pipe carries total project flow (unless a per-segment flow was
        # computed by the AI piping generator).
        stored = pipe.get('properties', {}).get('flow_lps')
        if stored is not None:
            total_flow = stored / 1000.0
        else:
            total_tree_count = mongo.db.trees.count_documents({'project_id': project_id})
            total_flow = zone_flow_demand_m3s(total_tree_count, emitter_flow, emitters_per_tree)

        validate_pipe_velocity(
            checks, pipe, total_flow,
            config['main_max_velocity_mps'], 'Main Pipe', lng, lat, config
        )

        # Pressure loss
        length_m = calculate_pipe_length_m(pipe.get('geometry'), lng, lat)
        diameter_mm = pipe.get('properties', {}).get('diameter', 110)
        validate_pressure_loss(
            checks, pipe, total_flow, length_m, diameter_mm,
            'Main Pipe', config['min_emitter_pressure_bar'], config
        )

        # Elevation head check for main pipe endpoint
        if elevation_model and water_source_elev is not None:
            _validate_elevation_head(
                checks, pipe, 'Main Pipe', elevation_model, water_source_elev, lng, lat, config
            )

    # === Sub pipes ===
    sub_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'sub_pipe'}))
    for pipe in sub_pipes:
        stored = pipe.get('properties', {}).get('flow_lps')
        if stored is not None:
            zone_flow = stored / 1000.0
        else:
            zone_id = pipe.get('properties', {}).get('zone_id')
            if zone_id:
                tree_count = mongo.db.trees.count_documents({'project_id': project_id, 'zone_id': zone_id})
                zone_flow = zone_flow_demand_m3s(tree_count, emitter_flow, emitters_per_tree)
            else:
                zone_flow = 0.001

        validate_pipe_velocity(
            checks, pipe, zone_flow,
            config['sub_max_velocity_mps'], 'Sub Pipe', lng, lat, config
        )

        length_m = calculate_pipe_length_m(pipe.get('geometry'), lng, lat)
        diameter_mm = pipe.get('properties', {}).get('diameter', 63)
        validate_pressure_loss(
            checks, pipe, zone_flow, length_m, diameter_mm,
            'Sub Pipe', config['min_emitter_pressure_bar'], config
        )

        # Elevation head check for sub pipe endpoint
        if elevation_model and water_source_elev is not None:
            _validate_elevation_head(
                checks, pipe, 'Sub Pipe', elevation_model, water_source_elev, lng, lat, config
            )

    # === Zone elevation pressure variation ===
    if elevation_model and elevation_model.get('stats', {}).get('range_m', 0) > 0:
        _validate_zone_elevation_pressure(checks, project_id, elevation_model, config, lng, lat)


def _validate_network_connectivity(checks, project_id, project, lng, lat):
    """Validate network connectivity using proximity-based graph analysis."""
    # Check that main pipe connects to water source
    water_source = project.get('water_source')
    main_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'main_pipe'}))

    # Project all main pipes once and index them for fast neighbour queries.
    main_geoms = [
        (p, projected_shape(p['geometry'], lng, lat))
        for p in main_pipes if p.get('geometry')
    ]
    if main_geoms:
        from shapely.strtree import STRtree
        main_tree = STRtree([g for _, g in main_geoms])
    else:
        main_tree = None

    if water_source and water_source.get('coordinates') and main_geoms:
        ws_projected, _ = project_geometry(water_source, lng, lat)
        ws_point = ws_projected if isinstance(ws_projected, Point) else Point(ws_projected)

        connected = False
        for _, pipe_geom in main_geoms:
            # Water source must lie on / very near the main pipe network
            # (works for both LineString and merged MultiLineString).
            try:
                if pipe_geom is not None and ws_point.distance(pipe_geom) < 10:
                    connected = True
                    break
            except Exception:
                continue

        if not connected:
            checks.append(HydraulicCheck(
                code='MAIN_PIPE_SOURCE_CONNECTION',
                severity='fail',
                object_type='network',
                object_id=project_id,
                message_en='Main pipe is not connected to water source (basin)',
                message_ar='الأنبوب الرئيسي غير متصل بمصدر المياه (الحوض)',
                recommendation_en='Redraw main pipe starting from water source location',
                recommendation_ar='أعد رسم الأنبوب الرئيسي بدءاً من موقع مصدر المياه'
            ))
        else:
            checks.append(HydraulicCheck(
                code='MAIN_PIPE_SOURCE_CONNECTION',
                severity='pass',
                object_type='network',
                object_id=project_id,
                message_en='Main pipe is connected to water source',
                message_ar='الأنبوب الرئيسي متصل بمصدر المياه'
            ))

    # Project all sub pipes once.
    sub_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'sub_pipe'}))
    zone_pipes = {id(p): (p, projected_shape(p['geometry'], lng, lat))
                  for p in sub_pipes if p.get('properties', {}).get('zone_id') and p.get('geometry')}

    for sub in sub_pipes:
        if not sub.get('geometry'):
            continue
        sub_geom = projected_shape(sub['geometry'], lng, lat)
        sub_start = Point(sub_geom.coords[0]) if sub_geom.coords else None
        if not sub_start:
            continue

        # Laterals (row-attached sub pipes) may tap a zone pipe instead of the MP
        parents = main_geoms if main_tree is not None else []
        if sub.get('properties', {}).get('row_id'):
            parents = parents + [px for px in zone_pipes.values() if px[0] is not sub]

        connected = False
        if main_tree is not None:
            for idx in main_tree.query(sub_start):
                if main_geoms[int(idx)][1].distance(sub_start) < 10:
                    connected = True
                    break
        if not connected and parents:
            for _, geom in parents:
                try:
                    if geom.distance(sub_start) < 10:
                        connected = True
                        break
                except Exception:
                    continue

        if not connected:
            checks.append(HydraulicCheck(
                code='SUB_PIPE_CONNECTION',
                severity='warning',
                object_type='network_element',
                object_id=sub['id'],
                message_en=f"Sub pipe {sub.get('name_en', '')} is not connected to main pipe",
                message_ar=f"الأنبوب الفرعي {sub.get('name_ar', '')} غير متصل بالأنبوب الرئيسي",
                recommendation_en='Redraw sub pipe starting from main pipe',
                recommendation_ar='أعد رسم الأنبوب الفرعي بدءاً من الأنبوب الرئيسي'
            ))


def _validate_valve_coverage(checks, project_id):
    """Validate that all required valves are present."""
    # Master valve (between basin and main pipe)
    master_valves = mongo.db.network_elements.count_documents({
        'project_id': project_id, 'type': 'master_valve'
    })
    if master_valves == 0:
        checks.append(HydraulicCheck(
            code='MASTER_VALVE_PRESENT',
            severity='fail',
            object_type='network',
            object_id=project_id,
            message_en='No master valve between basin and main pipe',
            message_ar='لا يوجد صمام رئيسي بين الحوض والأنبوب الرئيسي',
            recommendation_en='Add a master valve at the water source / main pipe connection',
            recommendation_ar='أضف صماماً رئيسياً عند اتصال مصدر المياه / الأنبوب الرئيسي'
        ))
    else:
        checks.append(HydraulicCheck(
            code='MASTER_VALVE_PRESENT',
            severity='pass',
            object_type='network',
            object_id=project_id,
            message_en=f'{master_valves} master valve(s) present',
            message_ar=f'{master_valves} صمام رئيسي موجود'
        ))

    # Sector valves - one per sector
    sectors = list(mongo.db.sectors.find({'project_id': project_id}))
    sector_valves = list(mongo.db.network_elements.find({
        'project_id': project_id, 'type': 'sector_valve'
    }))

    for sector in sectors:
        has_valve = any(
            v.get('properties', {}).get('sector_id') == sector['id']
            for v in sector_valves
        )
        if not has_valve:
            checks.append(HydraulicCheck(
                code='SECTOR_VALVE_PRESENT',
                severity='fail',
                object_type='sector',
                object_id=sector['id'],
                message_en=f"No valve for sector {sector.get('name_en', '')}",
                message_ar=f"لا يوجد صمام للقطاع {sector.get('name_ar', '')}",
                recommendation_en=f'Add a sector valve for {sector.get("name_en", "")}',
                recommendation_ar=f'أضف صمام قطاع لـ {sector.get("name_ar", "")}'
            ))
        else:
            checks.append(HydraulicCheck(
                code='SECTOR_VALVE_PRESENT',
                severity='pass',
                object_type='sector',
                object_id=sector['id'],
                message_en=f'Valve present for sector {sector.get("name_en", "")}',
                message_ar=f'صمام موجود للقطاع {sector.get("name_ar", "")}'
            ))

    # Zone valves - one per zone
    zones = list(mongo.db.zones.find({'project_id': project_id}))
    zone_valves = list(mongo.db.network_elements.find({
        'project_id': project_id, 'type': 'zone_valve'
    }))

    for zone in zones:
        has_valve = any(
            v.get('properties', {}).get('zone_id') == zone['id']
            for v in zone_valves
        )
        if not has_valve:
            checks.append(HydraulicCheck(
                code='ZONE_VALVE_PRESENT',
                severity='fail',
                object_type='zone',
                object_id=zone['id'],
                message_en=f"No valve for zone {zone.get('name_en', '')}",
                message_ar=f"لا يوجد صمام للمنطقة {zone.get('name_ar', '')}",
                recommendation_en=f'Add a zone valve for {zone.get("name_en", "")}',
                recommendation_ar=f'أضف صمام منطقة لـ {zone.get("name_ar", "")}'
            ))
        else:
            checks.append(HydraulicCheck(
                code='ZONE_VALVE_PRESENT',
                severity='pass',
                object_type='zone',
                object_id=zone['id'],
                message_en=f'Valve present for zone {zone.get("name_en", "")}',
                message_ar=f'صمام موجود للمنطقة {zone.get("name_ar", "")}'
            ))


def _validate_dripline_coverage(checks, project_id, config, lng, lat):
    """Validate drip line coverage for all tree rows."""
    rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    trees = list(mongo.db.trees.find({'project_id': project_id}))
    driplines = list(mongo.db.network_elements.find({
        'project_id': project_id, 'type': 'dripline'
    }))

    if not trees:
        checks.append(HydraulicCheck(
            code='TREES_EXIST',
            severity='warning',
            object_type='project',
            object_id=project_id,
            message_en='No trees defined - cannot validate drip coverage',
            message_ar='لا توجد أشجار محددة - لا يمكن التحقق من تغطية التنقيط'
        ))
        return

    for drip in driplines:
        row_id = drip.get('properties', {}).get('row_id')
        row = next((r for r in rows if r['id'] == row_id), None) if row_id else None
        validate_dripline(checks, drip, row, trees, config, lng, lat)

    # Check trees without drip lines
    if rows:
        rows_with_drips = set()
        for drip in driplines:
            rid = drip.get('properties', {}).get('row_id')
            if rid:
                rows_with_drips.add(rid)

        rows_without = [r for r in rows if r['id'] not in rows_with_drips]
        if rows_without:
            checks.append(HydraulicCheck(
                code='DRIPLINE_ROW_COVERAGE',
                severity='fail',
                object_type='project',
                object_id=project_id,
                message_en=f'{len(rows_without)} row(s) without drip lines',
                message_ar=f'{len(rows_without)} صف بدون خطوط تنقيط',
                metrics={'rows_without_dripline': len(rows_without)},
                recommendation_en='Add drip lines for all tree rows',
                recommendation_ar='أضف خطوط تنقيط لجميع صفوف الأشجار'
            ))


def _validate_water_source(checks, project_id, config):
    """Validate water source capacity against total demand."""
    emitter_flow = config['emitter_flow_lph']
    emitters_per_tree = config['emitters_per_tree']

    total_trees = mongo.db.trees.count_documents({'project_id': project_id})
    total_flow = zone_flow_demand_m3s(total_trees, emitter_flow, emitters_per_tree)

    validate_water_source_capacity(checks, project_id, total_flow, config)


def _validate_tree_coverage(checks, project_id, config):
    """Validate tree-related checks."""
    trees = list(mongo.db.trees.find({'project_id': project_id}))
    zones = list(mongo.db.zones.find({'project_id': project_id}))

    if not trees:
        return

    # Check all trees have 2 drips
    for tree in trees:
        drips = tree.get('drips_per_tree', 0)
        expected = config['emitters_per_tree']
        if drips != expected:
            checks.append(HydraulicCheck(
                code='TREE_DRIP_COUNT',
                severity='warning',
                object_type='tree',
                object_id=tree['id'],
                message_en=f"Tree {tree.get('name_en', '')} has {drips} drips (expected {expected})",
                message_ar=f"الشجرة {tree.get('name_ar', '')} لديها {drips} قطارات (متوقع {expected})",
                recommendation_en=f'Ensure {expected} drips per tree',
                recommendation_ar=f'تأكد من {expected} قطارات لكل شجرة'
            ))

    # Check tree variety is set
    for tree in trees[:50]:  # Check first 50 to avoid too many checks
        if not tree.get('variety_en') and not tree.get('variety_ar'):
            checks.append(HydraulicCheck(
                code='TREE_VARIETY_SET',
                severity='warning',
                object_type='tree',
                object_id=tree['id'],
                message_en=f"Tree {tree.get('name_en', '')} has no variety specified",
                message_ar=f"الشجرة {tree.get('name_ar', '')} لا يوجد نوع محدد",
                recommendation_en='Specify tree variety for all trees',
                recommendation_ar='حدد نوع الشجرة لجميع الأشجار'
            ))

    # Check trees per zone
    for zone in zones:
        zone_tree_count = mongo.db.trees.count_documents({
            'project_id': project_id, 'zone_id': zone['id']
        })
        if zone_tree_count == 0:
            checks.append(HydraulicCheck(
                code='ZONE_HAS_TREES',
                severity='warning',
                object_type='zone',
                object_id=zone['id'],
                message_en=f"Zone {zone.get('name_en', '')} has no trees",
                message_ar=f"المنطقة {zone.get('name_ar', '')} لا توجد بها أشجار",
                recommendation_en='Add tree rows and generate trees for this zone',
                recommendation_ar='أضف صفوف أشجار وأنشئ الأشجار لهذه المنطقة'
            ))


def get_last_validation(project_id):
    """Get the most recent validation result for a project."""
    result = mongo.db.engineering_validations.find_one(
        {'project_id': project_id},
        sort=[('created_at', -1)]
    )
    if result:
        result['_id'] = str(result['_id'])
    return result


def get_validation_history(project_id, limit=10):
    """Get validation history for a project."""
    results = list(mongo.db.engineering_validations.find(
        {'project_id': project_id}
    ).sort('created_at', -1).limit(limit))
    for r in results:
        r['_id'] = str(r['_id'])
    return results


def apply_recommended_diameters(project_id, config=None):
    """Apply AI-recommended pipe diameters based on flow calculations.

    This modifies project data - should be called with user confirmation.
    """
    if config is None:
        config = ENGINEERING_DEFAULTS.copy()

    project = mongo.db.projects.find_one({'id': project_id})
    if not project:
        return {'error': 'Project not found'}

    from irrigation.engineering.projection import get_project_centroid
    lng, lat = get_project_centroid(project)
    emitter_flow = config['emitter_flow_lph']
    emitters_per_tree = config['emitters_per_tree']

    changes = []

    # Main pipes
    total_tree_count = mongo.db.trees.count_documents({'project_id': project_id})
    total_flow = zone_flow_demand_m3s(total_tree_count, emitter_flow, emitters_per_tree)

    for pipe in mongo.db.network_elements.find({'project_id': project_id, 'type': 'main_pipe'}):
        rec_d = recommended_diameter_mm(
            total_flow, config['main_max_velocity_mps'],
            config['standard_diameters_mm']
        )
        current = pipe.get('properties', {}).get('diameter', 0)
        if rec_d != current:
            mongo.db.network_elements.update_one(
                {'id': pipe['id']},
                {'$set': {'properties.diameter': rec_d}}
            )
            changes.append({
                'element': pipe.get('name_en', f'Main Pipe {pipe["id"]}'),
                'old_diameter': current,
                'new_diameter': rec_d
            })

    # Sub pipes
    for pipe in mongo.db.network_elements.find({'project_id': project_id, 'type': 'sub_pipe'}):
        zone_id = pipe.get('properties', {}).get('zone_id')
        if zone_id:
            tc = mongo.db.trees.count_documents({'project_id': project_id, 'zone_id': zone_id})
            flow = zone_flow_demand_m3s(tc, emitter_flow, emitters_per_tree)
        else:
            flow = 0.001

        rec_d = recommended_diameter_mm(
            flow, config['sub_max_velocity_mps'],
            config['standard_diameters_mm']
        )
        current = pipe.get('properties', {}).get('diameter', 0)
        if rec_d != current:
            mongo.db.network_elements.update_one(
                {'id': pipe['id']},
                {'$set': {'properties.diameter': rec_d}}
            )
            changes.append({
                'element': pipe.get('name_en', f'Sub Pipe {pipe["id"]}'),
                'old_diameter': current,
                'new_diameter': rec_d
            })

    return {'changes': changes, 'total_changes': len(changes)}


def _validate_elevation_head(checks, pipe, pipe_type, elevation_model, source_elev, lng, lat, config):
    """Validate pressure implications of elevation difference between source and pipe endpoint."""
    if not pipe.get('geometry'):
        return

    pipe_geom = projected_shape(pipe['geometry'], lng, lat)
    if pipe_geom is None or pipe_geom.is_empty:
        return

    # Get elevation at pipe endpoint (farthest point along the run).
    _, last = _line_end_coords(pipe_geom)
    if last is None:
        return
    endpoint = Point(last)
    endpoint_elev = get_elevation_at_point(elevation_model, endpoint)

    if endpoint_elev is None:
        return

    # Calculate elevation head
    elev_diff = endpoint_elev - source_elev
    elev_head_m = max(0, elev_diff)  # Only uphill requires extra pressure
    elev_head_bar = elev_head_m / 10.197

    # Calculate friction loss
    flow_m3s = 0.001  # Default
    stored = pipe.get('properties', {}).get('flow_lps')
    if stored is not None:
        flow_m3s = stored / 1000.0
    elif pipe_type == 'Main Pipe':
        tree_count = mongo.db.trees.count_documents({'project_id': pipe['project_id']})
        flow_m3s = zone_flow_demand_m3s(
            tree_count, config['emitter_flow_lph'], config['emitters_per_tree']
        )
    elif pipe_type == 'Sub Pipe':
        zone_id = pipe.get('properties', {}).get('zone_id')
        if zone_id:
            tc = mongo.db.trees.count_documents({
                'project_id': pipe['project_id'], 'zone_id': zone_id
            })
            flow_m3s = zone_flow_demand_m3s(
                tc, config['emitter_flow_lph'], config['emitters_per_tree']
        )

    length_m = calculate_pipe_length_m(pipe.get('geometry'), lng, lat)
    diameter_mm = pipe.get('properties', {}).get('diameter', 63)
    hf = hazen_williams_head_loss_m(
        flow_m3s, length_m, diameter_mm, config['hazen_williams_c']
    )
    minor_loss = hf * config['minor_loss_factor']
    total_head = hf + minor_loss + elev_head_m
    total_pressure_bar = total_head / 10.197

    source_pressure = config['source_pressure_bar']
    remaining_pressure = source_pressure - total_pressure_bar

    severity = 'pass'
    msg_en = (
        f"{pipe_type}: elevation diff {elev_diff:.1f}m, "
        f"total head loss {total_head:.2f}m ({total_pressure_bar:.3f} bar), "
        f"remaining {remaining_pressure:.3f} bar"
    )
    msg_ar = (
        f"{pipe_type}: فرق الارتفاع {elev_diff:.1f}م, "
        f"إجمالي فقد الضغط {total_head:.2f}م ({total_pressure_bar:.3f} بار), "
        f"المتبقي {remaining_pressure:.3f} بار"
    )

    rec_en = ''
    rec_ar = ''

    if remaining_pressure < config['min_emitter_pressure_bar']:
        severity = 'fail'
        rec_en = (
            f"Insufficient pressure at endpoint. Elevation gain of {elev_head_m:.1f}m "
            f"requires {elev_head_bar:.3f} bar. Consider: pump boost, larger pipe, "
            f"or relocate water source higher."
        )
        rec_ar = (
            f"ضغط غير كافٍ عند النقطة النهائية. ارتفاع {elev_head_m:.1f}م "
            f"يتطلب {elev_head_bar:.3f} بار. فكر في: مضخة تعزيز، أنبوب أكبر، "
            f"أو نقل مصدر المياه لارتفاع أعلى."
        )
    elif remaining_pressure < source_pressure * 0.3:
        severity = 'warning'
        rec_en = (
            f"Pressure dropping to {remaining_pressure:.3f} bar. "
            f"Elevation consumes {elev_head_bar:.3f} bar of source pressure."
        )
        rec_ar = (
            f"الضغط ينخفض إلى {remaining_pressure:.3f} بار. "
            f"الارتفاع يستهلك {elev_head_bar:.3f} بار من ضغط المصدر."
        )

    checks.append(HydraulicCheck(
        code=f'{pipe_type.upper().replace(" ", "_")}_ELEVATION_HEAD',
        severity=severity,
        object_type='network_element',
        object_id=pipe['id'],
        message_en=msg_en,
        message_ar=msg_ar,
        metrics={
            'source_elevation_m': round(source_elev, 2),
            'endpoint_elevation_m': round(endpoint_elev, 2),
            'elevation_diff_m': round(elev_diff, 2),
            'elevation_head_m': round(elev_head_m, 2),
            'elevation_head_bar': round(elev_head_bar, 4),
            'friction_loss_m': round(hf + minor_loss, 3),
            'total_head_m': round(total_head, 3),
            'remaining_pressure_bar': round(remaining_pressure, 4)
        },
        recommendation_en=rec_en,
        recommendation_ar=rec_ar
    ))


def _validate_zone_elevation_pressure(checks, project_id, elevation_model, config, lng, lat):
    """Validate pressure variation within zones due to elevation differences."""
    zones = list(mongo.db.zones.find({'project_id': project_id}))
    max_variation_pct = config.get('max_zone_pressure_variation_pct', 20)

    for zone in zones:
        if not zone.get('polygon'):
            continue

        zone_shapely = projected_shape(zone['polygon'], lng, lat)
        elev_metrics = elevation_metrics_for_polygon(elevation_model, zone_shapely)

        if not elev_metrics or elev_metrics.get('sample_count', 0) < 2:
            continue

        zone_range_m = elev_metrics['range_m']
        zone_range_bar = zone_range_m / 10.197
        source_pressure = config['source_pressure_bar']
        variation_pct = (zone_range_bar / source_pressure * 100) if source_pressure > 0 else 0

        severity = 'pass'
        msg_en = (
            f"Zone {zone.get('name_en', '')}: elevation range {zone_range_m:.1f}m "
            f"({zone_range_bar:.3f} bar pressure variation, {variation_pct:.1f}%)"
        )
        msg_ar = (
            f"المنطقة {zone.get('name_ar', '')}: نطاق الارتفاع {zone_range_m:.1f}م "
            f"({zone_range_bar:.3f} بار تباين الضغط, {variation_pct:.1f}٪)"
        )

        rec_en = ''
        rec_ar = ''

        if variation_pct > max_variation_pct:
            severity = 'warning'
            rec_en = (
                f"Pressure variation {variation_pct:.1f}% exceeds limit {max_variation_pct}%. "
                f"Consider splitting this zone or using pressure-compensating emitters."
            )
            rec_ar = (
                f"تباين الضغط {variation_pct:.1f}٪ يتجاوز الحد {max_variation_pct}٪. "
                f"فكر في تقسيم هذه المنطقة أو استخدام قطرات تعويض الضغط."
            )
        elif variation_pct > max_variation_pct * 0.7:
            severity = 'warning'
            rec_en = f"Pressure variation approaching limit. Consider pressure-compensating emitters."
            rec_ar = f"تباين الضغط يقترب من الحد. فكر في قطرات تعويض الضغط."

        checks.append(HydraulicCheck(
            code='ZONE_ELEVATION_PRESSURE_VARIATION',
            severity=severity,
            object_type='zone',
            object_id=zone['id'],
            message_en=msg_en,
            message_ar=msg_ar,
            metrics={
                'min_elevation_m': round(elev_metrics['min_elevation_m'], 2),
                'max_elevation_m': round(elev_metrics['max_elevation_m'], 2),
                'mean_elevation_m': round(elev_metrics['mean_elevation_m'], 2),
                'elevation_range_m': round(zone_range_m, 2),
                'pressure_variation_bar': round(zone_range_bar, 4),
                'variation_pct': round(variation_pct, 1),
                'max_allowed_pct': max_variation_pct
            },
            recommendation_en=rec_en,
            recommendation_ar=rec_ar
        ))

