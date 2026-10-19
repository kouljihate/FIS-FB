from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.engineering.hydraulics import ENGINEERING_DEFAULTS
from irrigation.engineering.validation import (
    run_full_validation, get_last_validation, get_validation_history,
    apply_recommended_diameters
)
from irrigation.engineering.regeneration import (
    regenerate_sectors, regenerate_zones, get_regeneration_report
)
from irrigation.engineering.elevation import (
    get_or_build_elevation_model, build_elevation_heatmap_geojson,
    has_elevation_model, delete_elevation_model
)
import json

engineering_bp = Blueprint('engineering', __name__)


def get_project_or_redirect(project_id):
    from irrigation.access import require_project

    project = require_project(project_id)
    if not project:
        return None
    project['_id'] = str(project['_id'])
    return project


@engineering_bp.route('/<int:project_id>')
@login_required
def validation_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    # Get last validation
    last_validation = get_last_validation(project_id)
    history = get_validation_history(project_id, limit=5)

    # Get regeneration report
    regen_report = get_regeneration_report(project_id)

    return render_template('engineering/validation.html',
                           project=project,
                           validation=last_validation,
                           history=history,
                           regen_report=regen_report,
                           defaults=ENGINEERING_DEFAULTS)


@engineering_bp.route('/<int:project_id>/run', methods=['POST'])
@login_required
def run_validation(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    # Get config from form, falling back to defaults
    config = ENGINEERING_DEFAULTS.copy()

    try:
        emitter_flow = request.form.get('emitter_flow_lph', type=float)
        if emitter_flow:
            config['emitter_flow_lph'] = emitter_flow

        emitters_per_tree = request.form.get('emitters_per_tree', type=int)
        if emitters_per_tree:
            config['emitters_per_tree'] = emitters_per_tree

        source_pressure = request.form.get('source_pressure_bar', type=float)
        if source_pressure:
            config['source_pressure_bar'] = source_pressure

        source_flow = request.form.get('source_flow_lpm', type=float)
        if source_flow:
            config['source_flow_lpm'] = source_flow

        hw_c = request.form.get('hazen_williams_c', type=int)
        if hw_c:
            config['hazen_williams_c'] = hw_c

        main_v = request.form.get('main_max_velocity_mps', type=float)
        if main_v:
            config['main_max_velocity_mps'] = main_v

        sub_v = request.form.get('sub_max_velocity_mps', type=float)
        if sub_v:
            config['sub_max_velocity_mps'] = sub_v

        drip_v = request.form.get('drip_max_velocity_mps', type=float)
        if drip_v:
            config['drip_max_velocity_mps'] = drip_v
    except Exception as e:
        flash(f'Invalid parameter: {e} / معامل غير صالح', 'danger')

    result = run_full_validation(project_id, config)

    status = result.get('summary', {}).get('status', 'unknown')
    passes = result.get('summary', {}).get('passes', 0)
    warnings = result.get('summary', {}).get('warnings', 0)
    failures = result.get('summary', {}).get('failures', 0)

    if status == 'pass':
        flash(f'Validation passed: {passes} checks OK / اجتاز التحقق: {passes} فحوصات', 'success')
    elif status == 'warning':
        flash(f'Validation: {passes} passed, {warnings} warnings / التحقق: {passes} نجح، {warnings} تحذيرات', 'warning')
    else:
        flash(f'Validation: {failures} failures, {warnings} warnings / التحقق: {failures} فشل، {warnings} تحذيرات', 'danger')

    return redirect(url_for('engineering.validation_view', project_id=project_id))


@engineering_bp.route('/<int:project_id>/apply_diameters', methods=['POST'])
@login_required
def apply_diameters(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    config = ENGINEERING_DEFAULTS.copy()
    try:
        emitter_flow = request.form.get('emitter_flow_lph', type=float)
        if emitter_flow:
            config['emitter_flow_lph'] = emitter_flow
        source_flow = request.form.get('source_flow_lpm', type=float)
        if source_flow:
            config['source_flow_lpm'] = source_flow
    except Exception:
        pass

    result = apply_recommended_diameters(project_id, config)

    changes = result.get('changes', [])
    if changes:
        flash(f'Applied {len(changes)} diameter changes / تم تطبيق {len(changes)} تغيير قطر', 'success')
    else:
        flash('No diameter changes needed / لا توجد تغييرات قطر مطلوبة', 'info')

    return redirect(url_for('engineering.validation_view', project_id=project_id))


@engineering_bp.route('/<int:project_id>/regenerate_sectors', methods=['POST'])
@login_required
def ai_regenerate_sectors(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    n_sectors = request.form.get('n_sectors', default=4, type=int)
    sectors = regenerate_sectors(project_id, n_sectors)

    if sectors:
        flash(f'Auto regenerated {len(sectors)} sectors with optimal partitioning / الذكاء الاصطناعي أنشأ {len(sectors)} قطاعات بتقسيم أمثل', 'success')
    else:
        flash('Auto regeneration failed - check boundary / فشل إعادة الإنشاء - تحقق من الحدود', 'danger')

    return redirect(url_for('geometry.sectors_view', project_id=project_id))


@engineering_bp.route('/<int:project_id>/regenerate_zones', methods=['POST'])
@login_required
def ai_regenerate_zones(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    zones_per_sector = request.form.get('zones_per_sector', default=2, type=int)
    sector_id = request.form.get('sector_id', type=int)

    zones = regenerate_zones(project_id, sector_id, zones_per_sector)

    if zones:
        flash(f'Auto regenerated {len(zones)} zones with optimal partitioning / الذكاء الاصطناعي أنشأ {len(zones)} مناطق بتقسيم أمثل', 'success')
    else:
        flash('Auto regeneration failed / فشل إعادة الإنشاء', 'danger')

    return redirect(url_for('geometry.zones_view', project_id=project_id))


@engineering_bp.route('/<int:project_id>/report')
@login_required
def report(project_id):
    """API endpoint returning validation as JSON."""
    from irrigation.access import find_project

    project = find_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    validation = get_last_validation(project_id)
    regen = get_regeneration_report(project_id)

    return jsonify({
        'project_id': project_id,
        'validation': validation,
        'regeneration': regen
    })


@engineering_bp.route('/<int:project_id>/elevation/fetch', methods=['POST'])
@login_required
def fetch_elevation(project_id):
    """Fetch and cache terrain elevation data for a project."""
    from irrigation.access import require_project

    project = require_project(project_id)
    if not project:
        return redirect(url_for('engineering.validation_view', project_id=project_id))

    spacing = request.form.get('spacing_m', default=30, type=int)

    try:
        model = get_or_build_elevation_model(project_id, project, spacing_m=spacing)
        if model:
            stats = model.get('stats', {})
            flash(
                f"Elevation data fetched: {stats.get('sample_count', 0)} points, "
                f"range {stats.get('min_elevation_m', 0):.1f}-{stats.get('max_elevation_m', 0):.1f}m / "
                f"تم جلب بيانات الارتفاع: {stats.get('sample_count', 0)} نقطة",
                'success'
            )
        else:
            flash('Could not fetch elevation data - check boundary / تعذر جلب بيانات الارتفاع', 'warning')
    except Exception as e:
        flash(f'Elevation fetch error: {e} / خطأ في جلب الارتفاع', 'danger')

    return redirect(url_for('engineering.validation_view', project_id=project_id))


@engineering_bp.route('/<int:project_id>/elevation/heatmap')
@login_required
def elevation_heatmap(project_id):
    """Return elevation sample points as GeoJSON for map display."""
    from irrigation.access import find_project

    project = find_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    model = mongo.db.project_elevation_models.find_one({'project_id': project_id})
    if not model:
        return jsonify({'type': 'FeatureCollection', 'features': []})

    return jsonify(build_elevation_heatmap_geojson(model))


@engineering_bp.route('/<int:project_id>/elevation/clear', methods=['POST'])
@login_required
def clear_elevation(project_id):
    """Delete cached elevation model for a project."""
    delete_elevation_model(project_id)
    flash('Elevation data cleared / تم مسح بيانات الارتفاع', 'info')
    return redirect(url_for('engineering.validation_view', project_id=project_id))

@engineering_bp.route('/<int:project_id>/technical-report')
@login_required
def technical_report_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    last_validation = get_last_validation(project_id)
    regen_report = get_regeneration_report(project_id)
    return render_template('engineering/technical_report.html', project=project, validation=last_validation, regen_report=regen_report, defaults=ENGINEERING_DEFAULTS)
