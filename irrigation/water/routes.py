"""Water Resource pages: Well, Basin and Other.

The three sub-tabs share the layout of the Sectors page (header, a table with a
circle-button toolbar, per-row Edit / Rename modals and the map editor) and one
editor for the project's water sources.  They differ only in the ``type`` of
source they list:

* **Well**  - ``well``
* **Basin** - ``basin`` and ``reservoir`` (both hold the water)
* **Other** - every other kind: ``pump``, ``canal``, ``other``, unknown

A source lives in ``project.spec.water_sources`` and is mirrored in
``project.water``; the hydraulics, the KML import and the project settings page
all read it from there.  Each entry is::

    {'id': 1, 'type': 'well', 'name': 'Well 1', 'name_ar': 'بئر 1',
     'lat': 34.02, 'lng': -6.0, 'flow_lpm': 12.0, 'notes': '',
     'validated': False, 'depth_m': 60.0, 'diameter_mm': 400,
     'water_level_m': 18.5}

A basin carries its footprint (a JSON ring of ``[lng, lat]`` points) or its
length x width, plus a depth; the area and the volume in m³ are derived from
those on every render (:func:`_derived`), so they never go stale.

``id`` is stable for the life of the project (assigned on first use), so the
per-row URLs keep working after a delete, and the project settings page keeps
the id of each row it saves.
"""

from __future__ import annotations

import json

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import login_required

from irrigation.access import require_project
from irrigation.extensions import mongo

water_bp = Blueprint('water', __name__)

#: Kinds of the "Other" tab are every kind that Well and Basin do not list.
WELL_KINDS = ('well',)
BASIN_KINDS = ('basin', 'reservoir')

#: Sub-tabs: header wording, the kinds listed and the kinds the Add form offers.
TABS = {
    'well': {
        'key': 'well',
        'en': 'Well',
        'ar': 'بئر',
        'icon': 'bi-droplet',
        'endpoint': 'water.well',
        'kinds': WELL_KINDS,
        'types': (('well', 'Well / بئر'),),
        'draw': ['marker'],
    },
    'basin': {
        'key': 'basin',
        'en': 'Basin',
        'ar': 'حوض',
        'icon': 'bi-water',
        'endpoint': 'water.basin',
        'kinds': BASIN_KINDS,
        'types': (('basin', 'Basin / حوض'), ('reservoir', 'Reservoir / خزان')),
        # A basin is a footprint, so the map offers the polygon tool too.
        'draw': ['marker', 'polygon'],
    },
    'other': {
        'key': 'other',
        'en': 'Other',
        'ar': 'أخرى',
        'icon': 'bi-boxes',
        'endpoint': 'water.other',
        'kinds': None,
        'types': (('pump', 'Pump / مضخة'), ('canal', 'Canal / قناة'),
                  ('other', 'Other / أخرى')),
        'draw': ['marker'],
    },
}

#: Wording, badge colour and icon of every kind (also used by the tables).
KINDS = {
    'well': ('Well', 'بئر', 'primary', 'bi-droplet'),
    'basin': ('Basin', 'حوض', 'info', 'bi-bucket'),
    'reservoir': ('Reservoir', 'خزان', 'success', 'bi-water'),
    'pump': ('Pump', 'مضخة', 'warning', 'bi-lightning-charge'),
    'canal': ('Canal', 'قناة', 'secondary', 'bi-signpost-split'),
    'other': ('Other', 'أخرى', 'dark', 'bi-question-circle'),
}

#: Every kind the Edit modal offers, so a source can move between the tabs.
KIND_OPTIONS = (
    ('well', 'Well / بئر'),
    ('basin', 'Basin / حوض'),
    ('reservoir', 'Reservoir / خزان'),
    ('pump', 'Pump / مضخة'),
    ('canal', 'Canal / قناة'),
    ('other', 'Other / أخرى'),
)

#: A basin and a reservoir are both water bodies: they share their attributes.
_WATER_BODY_FIELDS = (
    {'key': 'length_m', 'en': 'Length (m)', 'ar': 'الطول', 'step': '0.1'},
    {'key': 'width_m', 'en': 'Width (m)', 'ar': 'العرض', 'step': '0.1'},
    {'key': 'depth_m', 'en': 'Depth (m)', 'ar': 'العمق', 'step': '0.1'},
    {'key': 'polygon', 'kind': 'polygon', 'en': 'Footprint polygon',
     'ar': 'مضلع المساحة',
     'hint': '[[lng,lat],[lng,lat],...] - draw it on the map or paste it'},
)

#: Type-specific attributes, edited in the Add / Edit modals and posted as
#: ``<kind>_<key>`` so a hidden group can never clash with the visible one.
FIELDS = {
    'well': (
        {'key': 'depth_m', 'en': 'Depth (m)', 'ar': 'العمق', 'step': '0.1'},
        {'key': 'diameter_mm', 'en': 'Diameter (mm)', 'ar': 'القطر', 'step': '1'},
        {'key': 'water_level_m', 'en': 'Static water level (m)',
         'ar': 'منسوب الماء الساكن', 'step': '0.1'},
    ),
    'basin': _WATER_BODY_FIELDS,
    'reservoir': _WATER_BODY_FIELDS,
    'pump': (
        {'key': 'power_kw', 'en': 'Power (kW)', 'ar': 'الاستطاعة', 'step': '0.1'},
        {'key': 'head_m', 'en': 'Rated head (m)', 'ar': 'الارتفاع', 'step': '0.1'},
    ),
    'canal': (
        {'key': 'length_m', 'en': 'Length (m)', 'ar': 'الطول', 'step': '0.1'},
        {'key': 'width_m', 'en': 'Width (m)', 'ar': 'العرض', 'step': '0.1'},
        {'key': 'depth_m', 'en': 'Depth (m)', 'ar': 'العمق', 'step': '0.1'},
    ),
    'other': (),
}

#: Attribute wording, unit and decimals used by the table's Specifications
#: column (the Other tab mixes pumps, canals and one-off sources).
SPEC_LABELS = (
    ('length_m', 'L', 'm', 1),
    ('width_m', 'W', 'm', 1),
    ('depth_m', 'D', 'm', 1),
    ('diameter_mm', 'Ø', 'mm', 0),
    ('water_level_m', 'WL', 'm', 1),
    ('power_kw', 'Power', 'kW', 1),
    ('head_m', 'Head', 'm', 1),
)
SPEC_LABEL_MAP = {key: (label, unit, digits)
                  for key, label, unit, digits in SPEC_LABELS}

#: Every attribute any kind can carry, used to prune a source whose type
#: changed and to notice an edit that invalidates it.
_ATTRIBUTE_KEYS = tuple({field['key'] for fields in FIELDS.values()
                         for field in fields})

#: Table columns added after Type, one set per tab.
COLUMNS = {
    'well': (
        {'key': 'depth_m', 'en': 'Depth (m)', 'ar': 'العمق'},
        {'key': 'diameter_mm', 'en': 'Diameter (mm)', 'ar': 'القطر', 'digits': 0},
        {'key': 'water_level_m', 'en': 'Static level (m)', 'ar': 'منسوب ساكن'},
    ),
    'basin': (
        {'key': 'size', 'en': 'Size L × W (m)', 'ar': 'الأبعاد'},
        {'key': 'depth_m', 'en': 'Depth (m)', 'ar': 'العمق'},
        {'key': 'area_m2', 'en': 'Area (m²)', 'ar': 'المساحة'},
        {'key': 'volume_m3', 'en': 'Estimate (m³)', 'ar': 'الحجم'},
    ),
    'other': (
        {'key': 'spec', 'en': 'Specifications', 'ar': 'المواصفات'},
    ),
}


# --------------------------------------------------------------------------
# Project and source helpers
# --------------------------------------------------------------------------
def get_project_or_redirect(project_id):
    project = require_project(project_id)
    if not project:
        return None
    project['_id'] = str(project['_id'])
    return project


def _sources(project):
    """The project's water sources, spec list first, ``project.water`` second."""
    spec = project.get('spec') or {}
    sources = spec.get('water_sources')
    if sources is None:
        sources = project.get('water') or []
    return [dict(source) for source in sources if isinstance(source, dict)]


def _normalise(sources):
    """``(sources, changed)`` with an id, ``name_ar`` and ``validated`` set.

    Ids are assigned once and kept afterwards, so a row URL still points at the
    same source after another source has been removed.
    """
    result = [dict(source) for source in sources if isinstance(source, dict)]
    changed = False
    used = {source['id'] for source in result
            if isinstance(source.get('id'), int)}
    nxt = (max(used) + 1) if used else 1
    for source in result:
        if not isinstance(source.get('id'), int):
            while nxt in used:
                nxt += 1
            source['id'] = nxt
            used.add(nxt)
            changed = True
        if 'name_ar' not in source:
            source['name_ar'] = ''
            changed = True
        if 'validated' not in source:
            source['validated'] = False
            changed = True
        kind = str(source.get('type') or 'other').strip().lower()
        if kind != source.get('type'):
            source['type'] = kind
            changed = True
    return result, changed


def _save_sources(project_id, project, sources):
    """Persist the source list on the project document.

    The legacy single ``water_source`` Point (read by the piping, the elevation
    models and the KML export) follows the first positioned source.
    """
    spec = dict(project.get('spec') or {})
    spec['water_sources'] = sources
    update = {'spec': spec, 'water': sources, 'water_source': {}}
    for source in sources:
        if source.get('lat') is None or source.get('lng') is None:
            continue
        update['water_source'] = {
            'type': 'Point',
            'coordinates': [float(source['lng']), float(source['lat'])],
        }
        break

    mongo.db.projects.update_one({'id': project_id}, {'$set': update})

    # Keep the document this request renders from in step with the write.
    project['spec'] = spec
    project['water'] = sources
    project.update({key: value for key, value in update.items()
                    if key not in ('spec', 'water')})


def _in_tab(source, tab):
    kind = str(source.get('type') or 'other').strip().lower()
    if tab['kinds'] is None:
        return kind not in WELL_KINDS + BASIN_KINDS
    return kind in tab['kinds']


def _tab_or_404(key):
    tab = TABS.get(str(key or '').strip().lower())
    if tab is None:
        abort(404)
    return tab


def _boundary_shape(project):
    """The land boundary as a shapely geometry, or ``None``."""
    boundary = project.get('boundary')
    if not boundary:
        return None
    try:
        from shapely.geometry import shape as shp_shape

        geom = shp_shape(boundary)
        return geom if geom.is_valid else geom.buffer(0)
    except Exception:
        return None


def _source_problem(source, boundary):
    """Why a source cannot feed the network, or ``None`` when it is valid."""
    try:
        lat = float(source.get('lat'))
        lng = float(source.get('lng'))
    except (TypeError, ValueError):
        return 'no position / بدون موقع'
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lng <= 180.0:
        return 'position out of range / الموقع خارج النطاق'
    if boundary is not None:
        from shapely.geometry import Point

        # A centimetre of tolerance, so a source drawn on the border counts.
        if not boundary.buffer(1e-7).covers(Point(lng, lat)):
            return 'outside the land boundary / خارج حدود الأرض'
    flow = source.get('flow_lpm')
    try:
        if flow is not None and float(flow) < 0:
            return 'negative flow / تدفق سالب'
    except (TypeError, ValueError):
        return 'flow is not a number / التدفق ليس رقما'

    polygon = source.get('polygon')
    if polygon is not None:
        if not isinstance(polygon, (list, tuple)) or len(polygon) < 3:
            return 'footprint needs 3+ points / المضلع يحتاج 3 نقاط أو أكثر'
        if _polygon_area_m2(polygon) is None:
            return 'footprint is not a valid ring / المضلع غير صالح'

    for key, label, _unit, _digits in SPEC_LABELS:
        value = _number(source.get(key))
        if value is not None and value < 0:
            return f'{label} is negative / القيمة سالبة'
    return None


def _map_center(project):
    """``[lat, lng]`` centre of the land boundary, or the app's default view."""
    boundary = project.get('boundary') or {}
    geometry = str(boundary.get('type') or '')
    coords = boundary.get('coordinates') or []
    points = []
    try:
        if geometry == 'Polygon' and coords:
            points = coords[0]
        elif geometry == 'MultiPolygon':
            points = [point for polygon in coords for point in (polygon[0] if polygon else [])]
        if points:
            lats = [float(point[1]) for point in points]
            lngs = [float(point[0]) for point in points]
            return [(min(lats) + max(lats)) / 2.0, (min(lngs) + max(lngs)) / 2.0]
    except (TypeError, ValueError, IndexError):
        pass
    return [34.0, -6.0]


def _number(value):
    """A float, or ``None`` when the value is missing or unreadable."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(value, digits=1):
    number = _number(value)
    if number is None:
        return '—'
    return f'{number:,.{digits}f}'


def _clean_polygon(data):
    """``[[lng, lat], ...]`` from a JSON ring, or ``None`` when it is invalid."""
    if not isinstance(data, (list, tuple)):
        return None
    ring = []
    for point in data:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        lng = _number(point[0])
        lat = _number(point[1])
        if lng is None or lat is None:
            return None
        if not -180.0 <= lng <= 180.0 or not -90.0 <= lat <= 90.0:
            return None
        ring.append([lng, lat])
    return ring or None


def _form_polygon(name):
    raw = (request.form.get(name) or '').strip()
    if not raw:
        return None
    try:
        return _clean_polygon(json.loads(raw))
    except ValueError:
        return None


def _form_attributes(kind):
    """The selected kind's attributes, read from its own form group."""
    values = {}
    for field in FIELDS.get(kind, ()):
        name = f"{kind}_{field['key']}"
        if field.get('kind') == 'polygon':
            values['polygon'] = _form_polygon(name)
        else:
            values[field['key']] = _form_float(name)
    return values


def _polygon_area_m2(ring):
    """Area of a ``[[lng, lat], ...]`` ring in m², or ``None``."""
    if not isinstance(ring, (list, tuple)) or len(ring) < 3:
        return None
    closed = [list(point) for point in ring]
    if closed[0] != closed[-1]:
        closed.append(closed[0])
    try:
        from irrigation.engineering.projection import project_geometry

        lng = sum(point[0] for point in closed) / len(closed)
        lat = sum(point[1] for point in closed) / len(closed)
        geom, _crs = project_geometry(
            {'type': 'Polygon', 'coordinates': [closed]}, lng, lat)
        return float(geom.area) if geom.area > 0 else None
    except Exception:
        return None


def _derived(source):
    """``area_m2`` and ``volume_m3`` from the footprint and the depth."""
    area = _polygon_area_m2(source.get('polygon') or [])
    if area is None:
        length = _number(source.get('length_m'))
        width = _number(source.get('width_m'))
        if length is not None and width is not None:
            area = length * width
    depth = _number(source.get('depth_m'))
    volume = area * depth if (area is not None and depth is not None) else None
    return {
        'area_m2': round(area, 2) if area is not None else None,
        'volume_m3': round(volume, 2) if volume is not None else None,
    }


def _spec_text(source):
    """One line listing whichever attributes the source carries."""
    parts = []
    for key, label, unit, digits in SPEC_LABELS:
        value = _number(source.get(key))
        if value is None:
            continue
        parts.append(f'{label} {value:,.{digits}f} {unit}')
    return ' · '.join(parts) or '—'


def _cell_text(source, column, derived):
    key = column['key']
    digits = column.get('digits', 1)
    if key == 'size':
        length = _number(source.get('length_m'))
        width = _number(source.get('width_m'))
        if length is None or width is None:
            return '—'
        return f'{length:,.1f} × {width:,.1f}'
    if key == 'spec':
        return _spec_text(source)
    if key in ('area_m2', 'volume_m3'):
        return _fmt_number(derived.get(key), digits)
    return _fmt_number(source.get(key), digits)


def _form_float(name):
    raw = (request.form.get(name) or '').strip().replace(',', '.')
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _form_text(name):
    return (request.form.get(name) or '').strip()


def _form_kind(fallback):
    kind = _form_text('type').lower()
    return kind if kind in KINDS else fallback


def _position_ok(lat, lng):
    """Both coordinates empty, or both inside the valid ranges."""
    if lat is None and lng is None:
        return True
    if lat is None or lng is None:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _source_by_id(sources, source_id):
    return next((source for source in sources
                 if source.get('id') == source_id), None)


# --------------------------------------------------------------------------
# Pages (one template, three tabs)
# --------------------------------------------------------------------------
def _render(tab_key, project_id):
    tab = TABS[tab_key]
    project = get_project_or_redirect(project_id)
    if project is None:
        return redirect(url_for('project.index'))

    # KML water bodies (a basin is often a polygon) belong in this list too.
    from irrigation.project.routes import _ensure_kml_water_sources

    _ensure_kml_water_sources(project)

    sources, changed = _normalise(_sources(project))
    if changed:
        _save_sources(project_id, project, sources)

    tab_sources = [source for source in sources if _in_tab(source, tab)]
    counts = {key: len([source for source in sources
                        if _in_tab(source, config)])
              for key, config in TABS.items()}

    # The table shows type-specific columns, so each row carries its rendered
    # cells: the formatting lives here, where it is testable.
    columns = COLUMNS[tab_key]
    rows = []
    for source in tab_sources:
        derived = _derived(source)
        row = dict(source)
        row['derived'] = derived
        row['cells'] = [_cell_text(source, column, derived)
                        for column in columns]
        rows.append(row)

    return render_template(
        'water/sources.html',
        project=project,
        tab=tab,
        tabs=TABS,
        kinds=KINDS,
        kind_options=KIND_OPTIONS,
        sources=rows,
        columns=columns,
        fields=FIELDS,
        kind_fields=tuple((kind, label, FIELDS.get(kind, ()))
                          for kind, label in KIND_OPTIONS),
        total_sources=len(sources),
        tab_counts=counts,
        has_boundary=_boundary_shape(project) is not None,
        map_center=_map_center(project),
        map_zoom=15,
    )


@water_bp.route('/<int:project_id>/well')
@login_required
def well(project_id):
    return _render('well', project_id)


@water_bp.route('/<int:project_id>/basin')
@login_required
def basin(project_id):
    return _render('basin', project_id)


@water_bp.route('/<int:project_id>/other')
@login_required
def other(project_id):
    return _render('other', project_id)


# --------------------------------------------------------------------------
# Table actions
# --------------------------------------------------------------------------
@water_bp.route('/<int:project_id>/<tab>/add', methods=['POST'])
@login_required
def source_add(project_id, tab):
    config = _tab_or_404(tab)
    project = get_project_or_redirect(project_id)
    if project is None:
        return redirect(url_for('project.index'))
    target = url_for(config['endpoint'], project_id=project_id)

    sources, _ = _normalise(_sources(project))
    kind = _form_kind(config['types'][0][0])
    lat = _form_float('lat')
    lng = _form_float('lng')
    if not _position_ok(lat, lng):
        flash('Enter both coordinates within their range / '
              'أدخل الإحداثيين معا داخل نطاقهما', 'danger')
        return redirect(target)

    source = {
        'type': kind,
        'name': _form_text('name'),
        'name_ar': _form_text('name_ar'),
        'lat': lat,
        'lng': lng,
        'flow_lpm': _form_float('flow_lpm'),
        'notes': _form_text('notes'),
        'validated': False,
    }
    source.update(_form_attributes(kind))
    sources.append(source)
    sources, _ = _normalise(sources)
    saved = sources[-1]
    if not saved['name']:
        saved['name'] = f"{KINDS[saved['type']][0]} {saved['id']}"
    _save_sources(project_id, project, sources)

    flash(f"Added {saved['name']} / تمت إضافة {saved['name']}", 'success')
    return redirect(target)


@water_bp.route('/<int:project_id>/<tab>/<int:source_id>/edit', methods=['POST'])
@login_required
def source_edit(project_id, tab, source_id):
    config = _tab_or_404(tab)
    project = get_project_or_redirect(project_id)
    if project is None:
        return redirect(url_for('project.index'))
    target = url_for(config['endpoint'], project_id=project_id)

    sources, _ = _normalise(_sources(project))
    source = _source_by_id(sources, source_id)
    if source is None:
        flash('Water source not found / مصدر المياه غير موجود', 'danger')
        return redirect(target)

    lat = _form_float('lat')
    lng = _form_float('lng')
    if not _position_ok(lat, lng):
        flash('Enter both coordinates within their range / '
              'أدخل الإحداثيين معا داخل نطاقهما', 'danger')
        return redirect(target)

    moved = (lat, lng) != (source.get('lat'), source.get('lng'))
    kind = _form_kind(source.get('type') or 'other')
    attributes = _form_attributes(kind)
    before = {key: source.get(key) for key in _ATTRIBUTE_KEYS}
    source.update({
        'type': kind,
        'name': _form_text('name') or source.get('name') or '',
        'name_ar': _form_text('name_ar'),
        'lat': lat,
        'lng': lng,
        'flow_lpm': _form_float('flow_lpm'),
        'notes': _form_text('notes'),
    })
    # A different type drops the attributes the new kind does not use.
    for key in _ATTRIBUTE_KEYS:
        source.pop(key, None)
    source.update(attributes)
    if moved or any(before.get(key) != source.get(key)
                    for key in _ATTRIBUTE_KEYS):
        source['validated'] = False
    _save_sources(project_id, project, sources)

    flash(f"{source['name']} updated / تم تحديث {source['name']}", 'success')
    return redirect(target)


@water_bp.route('/<int:project_id>/<tab>/<int:source_id>/rename', methods=['POST'])
@login_required
def source_rename(project_id, tab, source_id):
    config = _tab_or_404(tab)
    project = get_project_or_redirect(project_id)
    if project is None:
        return redirect(url_for('project.index'))
    target = url_for(config['endpoint'], project_id=project_id)

    sources, _ = _normalise(_sources(project))
    source = _source_by_id(sources, source_id)
    if source is None:
        flash('Water source not found / مصدر المياه غير موجود', 'danger')
        return redirect(target)

    name = _form_text('name')
    name_ar = _form_text('name_ar')
    if name:
        source['name'] = name
    source['name_ar'] = name_ar
    _save_sources(project_id, project, sources)

    flash(f"{source['name']} renamed / تمت إعادة تسمية {source['name']}",
          'success')
    return redirect(target)


@water_bp.route('/<int:project_id>/<tab>/remove', methods=['POST'])
@login_required
def source_remove(project_id, tab):
    config = _tab_or_404(tab)
    project = get_project_or_redirect(project_id)
    if project is None:
        return redirect(url_for('project.index'))
    target = url_for(config['endpoint'], project_id=project_id)

    wanted = set()
    for raw in request.form.getlist('source_ids'):
        try:
            wanted.add(int(raw))
        except ValueError:
            continue
    if not wanted:
        flash('Select at least one source to remove / '
              'اختر مصدرا واحدا على الأقل للإزالة', 'warning')
        return redirect(target)

    sources, _ = _normalise(_sources(project))
    kept = [source for source in sources if source['id'] not in wanted]
    removed = len(sources) - len(kept)
    if not removed:
        flash('No matching water sources found / '
              'لم يتم العثور على مصادر مياه مطابقة', 'warning')
        return redirect(target)

    _save_sources(project_id, project, kept)
    flash(f'Removed {removed} source(s) / تمت إزالة {removed} مصدر',
          'success')
    return redirect(target)


@water_bp.route('/<int:project_id>/<tab>/validate', methods=['POST'])
@login_required
def source_validate(project_id, tab):
    config = _tab_or_404(tab)
    project = get_project_or_redirect(project_id)
    if project is None:
        return redirect(url_for('project.index'))
    target = url_for(config['endpoint'], project_id=project_id)

    sources, _ = _normalise(_sources(project))
    rows = [source for source in sources if _in_tab(source, config)]
    if not rows:
        flash(f'No {config["en"]} source to validate / '
              f'لا يوجد مصدر {config["ar"]} للتحقق منه', 'warning')
        return redirect(target)

    boundary = _boundary_shape(project)
    valid = 0
    problems = []
    for source in rows:
        problem = _source_problem(source, boundary)
        source['validated'] = problem is None
        if problem is None:
            valid += 1
        else:
            label = source.get('name') or f"#{source['id']}"
            problems.append(f'{label}: {problem}')
    _save_sources(project_id, project, sources)

    if problems:
        flash(f'Validated {valid} of {len(rows)} {config["en"]} source(s). '
              f'Check: {"; ".join(problems[:3])}'
              f'{" …" if len(problems) > 3 else ""} / '
              f'تم التحقق من {valid} من {len(rows)} مصدر',
              'warning')
    else:
        flash(f'All {valid} {config["en"]} source(s) validated / '
              f'تم التحقق من كل المصادر ({valid})', 'success')
    return redirect(target)
