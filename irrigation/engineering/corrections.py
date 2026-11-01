"""Automatic corrections for engineering validation rules.

The Validation Rules table offers a one-click correction per rule.  Only the
rules whose recommendation names a concrete change can be corrected
automatically: a pipe that is too small gets the diameter its velocity check
recommends, and a missing valve is placed by the same generator the Components
page uses.  Everything else stays manual, and the page marks it as such.

Every fixer returns ``{applied, message_en, message_ar}`` so the route can tell
the user what happened.  Fixes change project data and only run on an explicit
POST; the caller re-runs the validation afterwards so the table reflects the
new state.
"""

from irrigation.extensions import mongo
from irrigation.engineering.hydraulics import ENGINEERING_DEFAULTS

VELOCITY_SUFFIX = '_VELOCITY'
PRESSURE_SUFFIX = '_PRESSURE_LOSS'

#: Missing-valve checks and the valve each one asks for.
VALVE_FIXES = {
    'MASTER_VALVE_PRESENT': 'master_valve',
    'SECTOR_VALVE_PRESENT': 'sector_valve',
    'ZONE_VALVE_PRESENT': 'zone_valve',
}


def _metrics(check):
    return (check or {}).get('metrics') or {}


def _next_diameter(current):
    """Smallest standard diameter larger than ``current`` (None when at the top)."""
    try:
        current = float(current)
    except (TypeError, ValueError):
        return None
    for diameter in ENGINEERING_DEFAULTS['standard_diameters_mm']:
        if diameter > current:
            return diameter
    return None


def can_auto_fix(check):
    """True when this rule has a correction the app can apply by itself."""
    if not check or check.get('severity') == 'pass':
        return False
    code = check.get('code') or ''
    if code in VALVE_FIXES:
        return True
    if code.endswith(VELOCITY_SUFFIX):
        # A warning whose recommendation is the diameter already in place has
        # nothing left to apply; leave it to the manual recommendation.
        metrics = _metrics(check)
        recommended = metrics.get('recommended_diameter_mm')
        return recommended is not None and recommended != metrics.get('diameter_mm')
    if code.endswith(PRESSURE_SUFFIX):
        return _next_diameter(_metrics(check).get('diameter_mm')) is not None
    return False


def _result(applied, message_en, message_ar):
    return {'applied': bool(applied), 'message_en': message_en, 'message_ar': message_ar}


def _find_element(project_id, check):
    return mongo.db.network_elements.find_one({
        'project_id': project_id,
        'id': check.get('object_id'),
    })


def _fix_diameter(project_id, check, diameter, summary_en, summary_ar):
    element = _find_element(project_id, check)
    if element is None:
        return _result(False,
                       'The checked pipe no longer exists / الأنبوب المفحوص لم يعد موجوداً')
    current = (element.get('properties') or {}).get('diameter')
    if current == diameter:
        return _result(False,
                       'The pipe already has the recommended diameter / القطر الحالي هو الموصى به')

    mongo.db.network_elements.update_one(
        {'project_id': project_id, 'id': element['id']},
        {'$set': {'properties.diameter': diameter}}
    )
    name = element.get('name_en') or ('Pipe %s' % element.get('id'))
    return _result(
        True,
        '%s: %s diameter %s mm -> %s mm' % (summary_en, name, current, diameter),
        '%s: قطر %s من %s مم إلى %s مم' % (summary_ar, name, current, diameter)
    )


def _fix_velocity(project_id, check):
    target = _metrics(check).get('recommended_diameter_mm')
    if target is None:
        return _result(False,
                       'No diameter recommendation for this check / لا توصية قطر لهذا الفحص')
    return _fix_diameter(project_id, check, target,
                         'Pipe velocity corrected', 'تم تصحيح سرعة الأنبوب')


def _fix_pressure_loss(project_id, check, config):
    metrics = _metrics(check)
    current = metrics.get('diameter_mm')
    loss_bar = metrics.get('pressure_loss_bar')
    source_bar = (config or ENGINEERING_DEFAULTS).get('source_pressure_bar')

    # Jump straight to the diameter that should bring the loss under the 20 %
    # warning threshold.  Hazen-Williams says head loss scales with 1 / d^4.87,
    # so one larger size would only shave off a fraction of an oversized loss.
    target_loss = source_bar * 0.2 if source_bar else None
    target = None
    if current and loss_bar and target_loss and loss_bar > target_loss:
        required = current * (loss_bar / target_loss) ** (1 / 4.87)
        for diameter in ENGINEERING_DEFAULTS['standard_diameters_mm']:
            if diameter >= required:
                target = diameter
                break
    if target is None:
        target = _next_diameter(current)
    if target is None:
        return _result(False,
                       'No larger standard diameter is available / لا يوجد قطر قياسي أكبر متاح')
    return _fix_diameter(project_id, check, target,
                         'Pressure loss reduced', 'تم تقليل فقد الضغط')


def _fix_valves(project_id, check):
    from irrigation.components.valve_generator import generate_valves

    wanted = VALVE_FIXES.get(check.get('code'))
    summary = generate_valves(project_id, replace=False)
    created = summary.get('created') or {}
    count = created.get(wanted or '', 0)
    if count:
        # The generator places every valve it finds missing, so report the whole
        # set instead of only the one this rule asked for.
        labels = {'master_valve': 'master valve', 'sector_valve': 'sector valve',
                  'zone_valve': 'zone valve'}
        parts = ['%d %s%s' % (number, labels.get(valve_type, valve_type.replace('_', ' ')),
                              '' if number == 1 else 's')
                 for valve_type, number in created.items() if number]
        total = sum(created.values())
        return _result(True,
                       'Valves added: ' + ', '.join(parts),
                       'تمت إضافة %d صمام' % total)
    warnings = summary.get('warnings') or []
    detail = (' ' + warnings[0]) if warnings else ''
    return _result(False,
                   'No valve could be placed automatically.' + detail,
                   'تعذر إنشاء الصمام تلقائياً - تحقق من الأنابيب والمصدر')


def apply_correction(project_id, check, config=None):
    """Run the correction for one validation rule; never raises."""
    code = (check or {}).get('code') or ''
    try:
        if code in VALVE_FIXES:
            return _fix_valves(project_id, check)
        if code.endswith(VELOCITY_SUFFIX):
            return _fix_velocity(project_id, check)
        if code.endswith(PRESSURE_SUFFIX):
            return _fix_pressure_loss(project_id, check, config)
    except Exception as exc:  # a failed correction must not break the page
        return _result(False,
                       'Correction failed: %s' % exc,
                       'فشل التصحيح: %s' % exc)
    return _result(False,
                   'This rule needs a manual correction / هذا الفحص يحتاج تصحيحاً يدوياً')
