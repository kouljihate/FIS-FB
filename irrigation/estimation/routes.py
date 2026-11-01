from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.engineering.hydraulics import calculate_pipe_length_m
from irrigation.engineering.projection import get_project_centroid, projected_shape

estimation_bp = Blueprint('estimation', __name__)


def get_project_or_redirect(project_id):
    from irrigation.access import require_project

    project = require_project(project_id)
    if not project:
        return None
    project['_id'] = str(project['_id'])
    return project


def _centroid_or_default(project):
    try:
        return get_project_centroid(project)
    except Exception:
        sectors = list(mongo.db.sectors.find({'project_id': project['id']}).limit(1))
        if sectors:
            try:
                return get_project_centroid(sectors[0])
            except Exception:
                pass
        return (-6.0, 34.0)  # fallback: matches the default map center


def _polygon_area_m2(geom, lng, lat):
    if not geom:
        return 0.0
    try:
        return projected_shape(geom, lng, lat).area
    except Exception:
        return 0.0


def compute_quantities(project):
    """Gather all measurable quantities for the whole-project quote."""
    project_id = project['id']
    lng, lat = _centroid_or_default(project)

    net = list(mongo.db.network_elements.find({'project_id': project_id}))
    trees_count = mongo.db.trees.count_documents({'project_id': project_id})
    sectors = list(mongo.db.sectors.find({'project_id': project_id}))
    zones = list(mongo.db.zones.find({'project_id': project_id}))

    lines = []  # each: {key, name_en, name_ar, quantity, unit, group}

    # Pipes & dripline grouped by type + diameter (lengths in meters)
    pipe_groups = {}
    for elem in net:
        etype = elem.get('type')
        props = elem.get('properties') or {}
        if etype in ('main_pipe', 'sub_pipe', 'dripline'):
            diameter = props.get('diameter') or ''
            key = f'{etype}_{diameter}'
            if key not in pipe_groups:
                label = {
                    'main_pipe': ('Main Pipe', 'الأنبوب الرئيسي'),
                    'sub_pipe': ('Zone / Lateral Pipe', 'أنبوب المنطقة / فرعي'),
                    'dripline': ('Dripline', 'خط التنقيط'),
                }[etype]
                pipe_groups[key] = {
                    'key': key,
                    'name_en': f'{label[0]} Ø{diameter}mm' if diameter else label[0],
                    'name_ar': f'{label[1]} {diameter}مم' if diameter else label[1],
                    'quantity': 0.0,
                    'unit': 'm',
                    'group': 'pipes',
                }
            pipe_groups[key]['quantity'] += calculate_pipe_length_m(elem.get('geometry'), lng, lat)
    lines.extend(sorted(pipe_groups.values(), key=lambda x: x['key']))

    # Valves & fittings counted by type
    point_labels = {
        'master_valve': ('Master Valve', 'صمام رئيسي', 'valves'),
        'sector_valve': ('Sector Valve', 'صمام قطاع', 'valves'),
        'zone_valve': ('Zone Valve', 'صمام منطقة', 'valves'),
        'check_valve': ('Check Valve', 'صمام منعكس', 'fittings'),
        'air_release': ('Air Release Valve', 'صمام تهوية', 'fittings'),
        'pressure_reducer': ('Pressure Reducer', 'خافض ضغط', 'fittings'),
        'reduction': ('Reduction', 'تقليل قطر', 'fittings'),
        'other': ('Other Fittings', 'ملحقات أخرى', 'fittings'),
    }
    point_counts = {}
    for elem in net:
        etype = elem.get('type')
        if etype in point_labels:
            point_counts[etype] = point_counts.get(etype, 0) + 1
    for etype, count in point_counts.items():
        en, ar, group = point_labels[etype]
        lines.append({
            'key': etype, 'name_en': en, 'name_ar': ar,
            'quantity': count, 'unit': 'unit', 'group': group,
        })

    # Trees
    lines.append({
        'key': 'trees', 'name_en': 'Trees', 'name_ar': 'الأشجار',
        'quantity': trees_count, 'unit': 'tree', 'group': 'planting',
    })

    # Context KPIs (informational, not priced lines)
    total_area = sum(
        s.get('area_m2') or _polygon_area_m2(s.get('polygon'), lng, lat)
        for s in sectors
    )
    zone_area = sum(
        z.get('area_m2') or _polygon_area_m2(z.get('polygon'), lng, lat)
        for z in zones
    )
    total_pipe_m = sum(l['quantity'] for l in lines if l['group'] == 'pipes')
    total_drip_m = sum(
        l['quantity'] for l in lines if l['key'].startswith('dripline')
    )

    kpis = {
        'sector_count': len(sectors),
        'zone_count': len(zones),
        'total_area_m2': total_area,
        'zone_area_m2': zone_area,
        'trees_count': trees_count,
        'total_pipe_m': total_pipe_m,
        'total_drip_m': total_drip_m,
    }
    return lines, kpis


def _get_saved_estimation(project):
    est = (project.get('spec') or {}).get('estimation') or {}
    return {
        'currency': est.get('currency', 'MAD'),
        'prices': est.get('prices') or {},
        'items': est.get('items') or [],
        'notes': est.get('notes', ''),
        'discount_pct': est.get('discount_pct', 0),
        'tax_pct': est.get('tax_pct', 0),
        'roi': est.get('roi') or {},
    }


def _as_float(value, default=0.0):
    try:
        return float(str(value).strip() or default)
    except (TypeError, ValueError):
        return default


def quote_totals(lines, estimation):
    """Priced totals of the quote: the project's investment."""
    subtotal = 0.0
    for line in lines:
        price = _as_float((estimation.get('prices') or {}).get(line['key']), 0)
        subtotal += price * _as_float(line.get('quantity'), 0)
    for item in estimation.get('items') or []:
        subtotal += _as_float(item.get('quantity'), 0) * _as_float(item.get('unit_price'), 0)

    discount_pct = _as_float(estimation.get('discount_pct'), 0)
    tax_pct = _as_float(estimation.get('tax_pct'), 0)
    discount_amount = subtotal * discount_pct / 100.0
    after_discount = subtotal - discount_amount
    tax_amount = after_discount * tax_pct / 100.0
    return {
        'subtotal': subtotal,
        'discount_amount': discount_amount,
        'tax_amount': tax_amount,
        'total': after_discount + tax_amount,
    }


#: Years of exploitation the ROI subtab reports on.
ROI_HORIZON_YEARS = 5

#: Per-tree / per-year assumptions used until the user saves their own.
ROI_DEFAULTS = {
    'yield_kg_per_tree': 25.0,   # kg of fruit per tree per year
    'price_per_kg': 3.0,         # sale price of one kg of fruit
    'cost_per_tree_year': 60.0,  # water, labour, inputs per tree per year
    'maintenance_pct': 3.0,      # yearly upkeep, as a % of the investment
    'other_annual_cost': 0.0,    # anything else, per year
}


def roi_assumptions(estimation):
    """Saved ROI assumptions, completed with the defaults."""
    saved = estimation.get('roi') or {}
    result = dict(ROI_DEFAULTS)
    for key, default in ROI_DEFAULTS.items():
        if saved.get(key) is not None:
            result[key] = _as_float(saved.get(key), default)
    return result


def compute_roi(lines, estimation, assumptions, trees_count):
    """Return on investment after ``ROI_HORIZON_YEARS`` years of exploitation.

    The investment is the priced quote (discount and tax applied).  Each year
    earns ``trees x yield x price`` and spends ``trees x cost per tree``, plus
    maintenance as a share of the investment and any other annual cost.  ROI is
    the net profit left once the investment is recovered, over the investment.
    """
    totals = quote_totals(lines, estimation)
    investment = totals['total']
    trees = int(trees_count or 0)

    annual_revenue = trees * assumptions['yield_kg_per_tree'] * assumptions['price_per_kg']
    maintenance_cost = investment * assumptions['maintenance_pct'] / 100.0
    annual_cost = (trees * assumptions['cost_per_tree_year']
                   + maintenance_cost
                   + assumptions['other_annual_cost'])
    annual_net = annual_revenue - annual_cost

    years = []
    cumulative = -investment
    for year in range(1, ROI_HORIZON_YEARS + 1):
        cumulative += annual_net
        years.append({
            'year': year,
            'revenue': annual_revenue,
            'cost': annual_cost,
            'net': annual_net,
            'cumulative': cumulative,
        })

    net_cash_5y = annual_net * ROI_HORIZON_YEARS
    # A tree planted today does not carry the investment alone: the project is
    # profitable only once the five years have repaid what was spent to build it.
    profit_5y = net_cash_5y - investment
    roi_pct = (profit_5y / investment * 100.0) if investment > 0 else None
    annualized_roi_pct = (roi_pct / ROI_HORIZON_YEARS) if roi_pct is not None else None
    payback_years = (investment / annual_net) if annual_net > 0 and investment > 0 else None
    margin_pct = (annual_net / annual_revenue * 100.0) if annual_revenue > 0 else None

    return {
        'horizon': ROI_HORIZON_YEARS,
        'currency': estimation.get('currency') or 'MAD',
        'investment': investment,
        'subtotal': totals['subtotal'],
        'discount_amount': totals['discount_amount'],
        'tax_amount': totals['tax_amount'],
        'trees_count': trees,
        'annual_revenue': annual_revenue,
        'annual_cost': annual_cost,
        'maintenance_cost': maintenance_cost,
        'annual_net': annual_net,
        'margin_pct': margin_pct,
        'net_cash_5y': net_cash_5y,
        'profit_5y': profit_5y,
        'roi_pct': roi_pct,
        'annualized_roi_pct': annualized_roi_pct,
        'payback_years': payback_years,
        'years': years,
    }


@estimation_bp.route('/<int:project_id>')
@login_required
def quote_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    lines, kpis = compute_quantities(project)
    estimation = _get_saved_estimation(project)
    return render_template(
        'estimation/quote.html',
        project=project,
        lines=lines,
        kpis=kpis,
        estimation=estimation,
    )


@estimation_bp.route('/<int:project_id>/save', methods=['POST'])
@login_required
def quote_save(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    prices = {}
    for key in request.form.getlist('price_key'):
        key = key.strip()
        if key:
            prices[key] = _as_float(request.form.get(f'price_{key}', 0))

    items = []
    descriptions = request.form.getlist('item_description')
    quantities = request.form.getlist('item_quantity')
    units = request.form.getlist('item_unit')
    unit_prices = request.form.getlist('item_unit_price')
    for i, desc in enumerate(descriptions):
        desc = desc.strip()
        if not desc:
            continue
        items.append({
            'description': desc,
            'quantity': _as_float(quantities[i] if i < len(quantities) else 0, 1),
            'unit': (units[i].strip() if i < len(units) else '') or 'unit',
            'unit_price': _as_float(unit_prices[i] if i < len(unit_prices) else 0),
        })

    estimation = {
        'currency': request.form.get('currency', 'MAD').strip() or 'MAD',
        'prices': prices,
        'items': items,
        'notes': request.form.get('notes', '').strip(),
        'discount_pct': _as_float(request.form.get('discount_pct'), 0),
        'tax_pct': _as_float(request.form.get('tax_pct'), 0),
    }
    # Saving the quote must not throw away the ROI assumptions stored next to it.
    saved_roi = ((project.get('spec') or {}).get('estimation') or {}).get('roi')
    if saved_roi:
        estimation['roi'] = saved_roi
    mongo.db.projects.update_one(
        {'id': project_id},
        {'$set': {'spec.estimation': estimation}}
    )
    flash('Estimation saved / تم حفظ التقدير', 'success')
    return redirect(url_for('estimation.quote_view', project_id=project_id))


@estimation_bp.route('/<int:project_id>/roi')
@login_required
def roi_view(project_id):
    """Return on investment after five years of exploitation."""
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    lines, kpis = compute_quantities(project)
    estimation = _get_saved_estimation(project)
    assumptions = roi_assumptions(estimation)
    trees_count = mongo.db.trees.count_documents({'project_id': project_id})
    roi = compute_roi(lines, estimation, assumptions, trees_count)
    return render_template(
        'estimation/roi.html',
        project=project,
        estimation=estimation,
        assumptions=assumptions,
        roi=roi,
        kpis=kpis,
    )


@estimation_bp.route('/<int:project_id>/roi/save', methods=['POST'])
@login_required
def roi_save(project_id):
    """Store the exploitation assumptions used by the ROI subtab."""
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    saved = {}
    for key, default in ROI_DEFAULTS.items():
        value = _as_float(request.form.get(key), default)
        saved[key] = max(0.0, value)
    # A share above 100% of the investment per year makes no sense.
    saved['maintenance_pct'] = min(100.0, saved['maintenance_pct'])

    mongo.db.projects.update_one(
        {'id': project_id},
        {'$set': {'spec.estimation.roi': saved}}
    )
    flash('ROI assumptions saved / تم حفظ افتراضات العائد على الاستثمار', 'success')
    return redirect(url_for('estimation.roi_view', project_id=project_id))
