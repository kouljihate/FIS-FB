"""Context of the DPA technical report (Rapport Technique).

The report is one Jinja2 template; every figure it prints is read from the
project's own entries -- water resources, sectors / zones / rows / trees,
pipes, components and the saved estimation -- completed by the header, footer
and document scans kept in ``project.spec.dpa`` and edited on the report
settings page.

Nothing here writes to the project: ``build_context`` only reads, and
``save_settings`` stores the report's own entries.
"""

from __future__ import annotations

import datetime
import math
import os
from collections import Counter

from flask import current_app
from werkzeug.utils import secure_filename

from irrigation.engineering.hydraulics import (
    hazen_williams_head_loss_m,
    pipe_velocity_mps,
)
from irrigation.engineering.projection import get_project_centroid, projected_shape
from irrigation.extensions import mongo

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MONTHS_FR = ("Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
             "Août", "Septembre", "Octobre", "Novembre", "Décembre")
MONTHS_SHORT_FR = ("Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.",
                   "Août", "Sept.", "Oct.", "Nov.", "Déc.")
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

#: Reference evapotranspiration (mm/day) and effective rainfall (mm/month),
#: January first.  A project can replace them on the report settings page.
DEFAULT_ETO_MM_DAY = (1.5, 2.0, 2.7, 3.2, 4.2, 5.3, 6.6, 6.3, 4.9, 3.2, 2.0, 1.0)
DEFAULT_RAIN_MM = (30.0, 28.0, 30.0, 30.0, 15.0, 5.0, 2.0, 3.0, 12.0, 30.0,
                   40.0, 38.0)

#: Crop coefficient of the usual Moroccan orchards, matched on the variety
#: name.  An unknown variety falls back to ``default``.
DEFAULT_KC = {
    "olive": 0.65, "olivier": 0.65,
    "almond": 0.85, "amandier": 0.85,
    "citrus": 0.70, "orange": 0.70, "clementine": 0.70, "citronnier": 0.70,
    "vine": 0.65, "vigne": 0.65,
    "cerisier": 0.90, "caroubier": 0.55, "caroub": 0.55, "fig": 0.75,
    "figuier": 0.75, "pommier": 0.90, "prunier": 0.85, "grenadier": 0.80,
    "default": 0.80,
}

#: Application efficiency of the localised irrigation.
APPLICATION_EFFICIENCY = 0.90

#: Report settings saved under ``project.spec.dpa``.
DEFAULT_SETTINGS = {
    "company": {
        "name": "", "address": "", "city": "", "phone": "", "email": "",
        "logo": None,
    },
    "legal": {"ice": "", "if": "", "patente": "", "rc": "", "cnss": ""},
    "meta": {
        "title": "RAPPORT TECHNIQUE",
        "subtitle": "ETUDE D'EQUIPEMENT EN MATERIEL D'IRRIGATION LOCALISEE",
        "ministry": ("ROYAUME DU MAROC\nMINISTERE DE L'AGRICULTURE, DE LA "
                     "PECHE MARITIME,\nDU DEVELOPPEMENT RURAL ET DES EAUX "
                     "ET FORETS"),
        "reference": "",
        "date": "",
        "cr": "",
        "province": "",
        "perimeter": "",
    },
    "exploitation": {
        "client": "", "cin": "", "address": "", "total_area_ha": "",
        "net_area_ha": "", "soil": "", "permeability_mm_h": "",
        "topography": "", "water_note": "",
    },
    "water": {
        "flow_m3h": "", "station_head_mce": 25.0, "min_pressure_mce": 10.0,
        "pump_efficiency": 0.70, "motor_margin_pct": 20,
        "motor_efficiency": 0.75, "dripline_diameter_mm": 16,
        "talus": 1.0, "revanche_m": 0.30, "pumping_hours_day": 20,
    },
    "climate": {
        "eto_mm_day": list(DEFAULT_ETO_MM_DAY),
        "rain_mm": list(DEFAULT_RAIN_MM),
    },
    "essai": {"brand": "", "model": "", "pressure_mce": "", "flow_lph": ""},
    "documents": {},
    "notes": "",
}

#: Document slots of the report, in printing order.
DOC_SLOTS = (
    ("cin_front", "CIN (recto)"),
    ("cin_back", "CIN (verso)"),
    ("cnss", "Attestation CNSS client"),
    ("well", "Attestation de creusement du puits"),
    ("supplier", "Engagement fournisseur"),
    ("qualification", "Certificat de qualification et de classification"),
    ("plan_sectors", "Plan de sectorisation"),
    ("plan_network", "Plan de réseau"),
    ("plan_basin", "Plan de bassin"),
    ("plan_head", "Plan de station de tête"),
    ("bulletin", "Bulletin d'essai"),
    ("catalogue", "Catalogue"),
)
DOC_SLOT_LABELS = dict(DOC_SLOTS)

ALLOWED_DOC_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _f(value, default=0.0):
    """Float value, or ``default`` when the entry is empty/not a number."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value, digits=2):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _median(values):
    values = sorted(v for v in (_f(v, None) for v in values) if v is not None)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def _merge(base, override):
    """Deep merge ``override`` onto a copy of ``base`` (defaults preserved)."""
    result = {key: (dict(value) if isinstance(value, dict) else value)
              for key, value in base.items()}
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _climate_values(raw, fallback):
    """Twelve monthly values from a list or a comma separated string."""
    if isinstance(raw, str):
        raw = [part for part in raw.replace(";", ",").split(",")]
    values = []
    for index in range(12):
        value = _f(raw[index], None) if isinstance(raw, (list, tuple)) and index < len(raw) else None
        values.append(fallback[index] if value is None else value)
    return values


def _kc_for(variety):
    name = str(variety or "").strip().lower()
    if name in DEFAULT_KC:
        return DEFAULT_KC[name]
    for key, value in DEFAULT_KC.items():
        if key != "default" and key in name:
            return value
    return DEFAULT_KC["default"]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def settings_for(project):
    """Report settings of a project, defaults filled in."""
    raw = (project.get("spec") or {}).get("dpa") or {}
    settings = _merge(DEFAULT_SETTINGS, raw)
    settings["climate"]["eto_mm_day"] = _climate_values(
        (raw.get("climate") or {}).get("eto_mm_day") or settings["climate"]["eto_mm_day"],
        DEFAULT_ETO_MM_DAY,
    )
    settings["climate"]["rain_mm"] = _climate_values(
        (raw.get("climate") or {}).get("rain_mm") or settings["climate"]["rain_mm"],
        DEFAULT_RAIN_MM,
    )
    settings["documents"] = {key: value for key, value in
                             (settings.get("documents") or {}).items() if value}
    return settings


def _save_document(project_id, slot, file_storage):
    """Store one uploaded document; returns its path inside ``uploads``."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        return None
    name = secure_filename(file_storage.filename) or "document"
    extension = os.path.splitext(name)[1].lower()
    if extension not in ALLOWED_DOC_EXTENSIONS:
        return None
    folder = os.path.join(current_app.config.get("UPLOAD_FOLDER", "uploads"),
                          "dpa", str(project_id))
    os.makedirs(folder, exist_ok=True)
    stored = f"{slot}{extension}"
    file_storage.save(os.path.join(folder, stored))
    return f"dpa/{project_id}/{stored}"


def save_settings(project_id, project, form, files):
    """Persist the report header/footer, cover and document scans."""
    spec = dict(project.get("spec") or {})
    current = spec.get("dpa") or {}
    settings = _merge(DEFAULT_SETTINGS, current)

    text_fields = {
        "company": {"name": "company_name", "address": "company_address",
                    "city": "company_city", "phone": "company_phone",
                    "email": "company_email"},
        "legal": {"ice": "legal_ice", "if": "legal_if",
                  "patente": "legal_patente", "rc": "legal_rc",
                  "cnss": "legal_cnss"},
        "meta": {"title": "meta_title", "subtitle": "meta_subtitle",
                 "ministry": "meta_ministry", "reference": "meta_reference",
                 "date": "meta_date", "cr": "meta_cr",
                 "province": "meta_province", "perimeter": "meta_perimeter"},
        "exploitation": {"client": "exp_client", "cin": "exp_cin",
                         "address": "exp_address",
                         "total_area_ha": "exp_total_area_ha",
                         "net_area_ha": "exp_net_area_ha",
                         "soil": "exp_soil",
                         "permeability_mm_h": "exp_permeability",
                         "topography": "exp_topography",
                         "water_note": "exp_water_note"},
        "essai": {"brand": "essai_brand", "model": "essai_model",
                  "pressure_mce": "essai_pressure", "flow_lph": "essai_flow"},
    }
    for section, mapping in text_fields.items():
        values = settings.setdefault(section, {})
        for key, field in mapping.items():
            if field in form:
                values[key] = str(form.get(field) or "").strip()

    water = settings.setdefault("water", {})
    for key, field, default in (
        ("flow_m3h", "water_flow_m3h", ""),
        ("station_head_mce", "water_station_head_mce", 25.0),
        ("min_pressure_mce", "water_min_pressure_mce", 10.0),
        ("pump_efficiency", "water_pump_efficiency", 0.70),
        ("motor_margin_pct", "water_motor_margin_pct", 20),
        ("motor_efficiency", "water_motor_efficiency", 0.75),
        ("dripline_diameter_mm", "water_dripline_diameter_mm", 16),
        ("talus", "water_talus", 1.0),
        ("revanche_m", "water_revanche_m", 0.30),
        ("pumping_hours_day", "water_pumping_hours_day", 20),
    ):
        if field in form:
            raw = str(form.get(field) or "").strip()
            water[key] = raw if key == "flow_m3h" else (_f(raw, default))

    climate = settings.setdefault("climate", {})
    if "climate_eto" in form:
        climate["eto_mm_day"] = _climate_values(form.get("climate_eto"),
                                                DEFAULT_ETO_MM_DAY)
    if "climate_rain" in form:
        climate["rain_mm"] = _climate_values(form.get("climate_rain"),
                                             DEFAULT_RAIN_MM)

    if "notes" in form:
        settings["notes"] = str(form.get("notes") or "").strip()

    documents = dict(settings.get("documents") or {})
    logo = _save_document(project_id, "logo", files.get("company_logo"))
    if logo:
        settings.setdefault("company", {})["logo"] = logo
    for slot, _label in DOC_SLOTS:
        stored = _save_document(project_id, slot, files.get(f"doc_{slot}"))
        if stored:
            documents[slot] = stored
    settings["documents"] = documents

    spec["dpa"] = settings
    mongo.db.projects.update_one({"id": project_id}, {"$set": {"spec": spec}})
    project["spec"] = spec
    return settings


# ---------------------------------------------------------------------------
# Project geometry
# ---------------------------------------------------------------------------
def _sectors(project):
    return [sector for sector in (project.get("sectors") or [])
            if isinstance(sector, dict)]


def _zones(sectors):
    zones = []
    for sector in sectors:
        for zone in sector.get("zones") or []:
            if isinstance(zone, dict):
                zone = dict(zone)
                zone["sector_name"] = sector.get("short_name") or sector.get("name_en") or ""
                zones.append(zone)
    return zones


def _rows(zones):
    return [row for zone in zones for row in (zone.get("rows") or [])
            if isinstance(row, dict)]


def _trees(rows):
    return [tree for row in rows for tree in (row.get("trees") or [])
            if isinstance(tree, dict)]


def _polygon_area(geom, lng, lat):
    if not geom:
        return 0.0
    try:
        return float(projected_shape(geom, lng, lat).area)
    except Exception:
        return 0.0


def _pipe_length(pipe, lng, lat):
    length = _f((pipe.get("properties") or {}).get("length_m"), 0.0)
    if length:
        return length
    geometry = pipe.get("geometry") or {}
    coords = geometry.get("coordinates") or []
    if len(coords) < 2 or not lng:
        return 0.0
    from irrigation.engineering.hydraulics import calculate_pipe_length_m

    try:
        return calculate_pipe_length_m(geometry, lng, lat)
    except Exception:
        return 0.0


def _elevation_of(zone):
    metrics = zone.get("elevation_metrics") or {}
    return _f(metrics.get("mean_elevation_m"), None)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------
def _crops(trees, net_area_ha, emitter_lph, eto_days, row_spacing=0.0):
    counts = Counter((tree.get("variety_en") or "Culture").strip() for tree in trees)
    total = sum(counts.values())
    peak_index = max(range(12), key=lambda index: eto_days[index])
    peak_eto = eto_days[peak_index]

    crops = []
    for variety, count in counts.most_common():
        sample = next((tree for tree in trees
                       if (tree.get("variety_en") or "").strip() == variety), {})
        share = (count / total) if total else 0.0
        spacing_tree = _f(sample.get("spacing_m"), row_spacing) or row_spacing
        drips = int(_f(sample.get("drips_per_tree"), 0.0)) or 2
        kc = _kc_for(variety)
        bb = kc * peak_eto / APPLICATION_EFFICIENCY
        pf = (drips * emitter_lph / (spacing_tree * row_spacing)
              if spacing_tree and row_spacing else None)
        hours = (bb / pf) if pf else None
        crops.append({
            "variety": variety,
            "trees": count,
            "share_pct": _round(share * 100, 2),
            "area_ha": _round(net_area_ha * share, 4),
            "spacing_m": _round(spacing_tree, 2) if spacing_tree else None,
            "row_spacing_m": _round(row_spacing, 2) if row_spacing else None,
            "drips_per_tree": drips,
            "emitter_lph": emitter_lph,
            "q_plant_lph": _round(drips * emitter_lph, 2),
            "kc": kc,
            "bb_mm_day": _round(bb, 2),
            "pf_mm_h": _round(pf, 2),
            "hours": _round(hours, 2),
            "peak_month": MONTHS_FR[peak_index],
        })
    return crops, peak_index


def _posts(zones, emitter_lph, peak_bb_mm, config):
    emitters_per_tree = int(_f(config.get("emitters_per_tree"), 1)) or 1
    posts = []
    for zone in zones:
        trees = [tree for row in (zone.get("rows") or [])
                 for tree in (row.get("trees") or []) if isinstance(tree, dict)]
        if not trees and not zone.get("area_m2"):
            continue
        drips = sum(int(_f(tree.get("drips_per_tree"), emitters_per_tree) or emitters_per_tree)
                    for tree in trees) or len(trees) * emitters_per_tree
        flow_m3h = drips * emitter_lph / 1000.0
        area = _f(zone.get("area_m2"), 0.0)
        volume = peak_bb_mm * area / 1000.0
        hours = (volume / flow_m3h) if flow_m3h else None
        varieties = sorted({(tree.get("variety_en") or "Culture").strip()
                            for tree in trees})
        posts.append({
            "sector": zone.get("sector_name") or "",
            "zone": zone.get("short_name") or zone.get("name_en") or "",
            "crop": " / ".join(varieties),
            "area_m2": _round(area, 0),
            "trees": len(trees),
            "drippers": drips,
            "flow_m3h": _round(flow_m3h, 2),
            "hours": _round(hours, 2),
            "zone_id": zone.get("id"),
            "elevation": _elevation_of(zone),
        })
    return posts


def _rampes(zones, emitter_lph, dripline_diameter, lng, lat):
    rampes = []
    for zone in zones:
        rows = [row for row in (zone.get("rows") or []) if isinstance(row, dict)]
        if not rows:
            continue
        length = sum(_pipe_length({"geometry": row.get("geometry"),
                                  "properties": {"length_m": row.get("length_m")}},
                                 lng, lat) for row in rows)
        trees = [tree for row in rows for tree in (row.get("trees") or [])]
        drips = sum(int(_f(tree.get("drips_per_tree"), 2) or 2) for tree in trees)
        flow_lph = drips * emitter_lph
        spacing = _median([row.get("spacing_m") for row in rows])
        rampes.append({
            "zone": zone.get("short_name") or zone.get("name_en") or "",
            "sector": zone.get("sector_name") or "",
            "rows": len(rows),
            "length_m": _round(length, 1),
            "spacing_m": _round(spacing, 2) if spacing else None,
            "drippers": drips,
            "flow_lph": _round(flow_lph, 1),
            "flow_m3h": _round(flow_lph / 1000.0, 2),
            "diameter_mm": dripline_diameter,
        })
    return rampes


def _pipe_rows(pipes, zone_flows, total_flow_m3h, lng, lat, pipe_flows=None,
               pipe_sectors=None):
    pipe_flows = pipe_flows or {}
    pipe_sectors = pipe_sectors or {}
    rows = []
    for pipe in pipes:
        props = pipe.get("properties") or {}
        diameter = _f(props.get("diameter"), 0.0)
        length = _pipe_length(pipe, lng, lat)
        stored = _f(props.get("flow_lps"), None)
        if stored is not None:
            flow_m3h = stored * 3.6
        elif pipe.get("type") == "sub_pipe":
            flow_m3h = zone_flows.get(props.get("zone_id"), 0.0)
        elif pipe.get("id") in pipe_flows:
            flow_m3h = pipe_flows[pipe["id"]]
        else:
            flow_m3h = total_flow_m3h
        flow_m3s = flow_m3h / 3600.0
        velocity = pipe_velocity_mps(flow_m3s, diameter) if diameter else None
        loss = (hazen_williams_head_loss_m(flow_m3s, length, diameter) * 1.10
                if diameter and length else 0.0)
        rows.append({
            "id": pipe.get("id"),
            "name": pipe.get("name_en") or pipe.get("short_name") or "",
            "type": pipe.get("type"),
            "zone_id": props.get("zone_id"),
            "sector_id": pipe_sectors.get(pipe.get("id")),
            "length_m": _round(length, 1),
            "diameter_mm": _round(diameter, 0),
            "flow_m3h": _round(flow_m3h, 2),
            "velocity_ms": _round(velocity, 2) if velocity is not None else None,
            "head_loss_m": _round(loss, 2),
        })
    return rows


def _pipe_centroid(pipe):
    coords = [point for point in ((pipe.get("geometry") or {}).get("coordinates") or [])
              if isinstance(point, (list, tuple)) and len(point) >= 2]
    if not coords:
        return None
    return (sum(point[0] for point in coords) / len(coords),
            sum(point[1] for point in coords) / len(coords))


def _trunk_flows(second_pipes, sectors, zone_sector, posts):
    """Flow of each second pipe: the demand of the sector it runs to.

    The network does not record its topology, so each second pipe is attached
    to the nearest sector polygon; that sector's peak demand is what the pipe
    carries.  A pipe with no geometry falls back to the project total.
    """
    from shapely.geometry import Point, shape

    demand = {}
    for post in posts:
        sector_id = zone_sector.get(post.get("zone_id"))
        demand[sector_id] = demand.get(sector_id, 0.0) + _f(post.get("flow_m3h"), 0.0)

    sector_shapes = []
    for sector in sectors:
        polygon = sector.get("polygon")
        if not polygon:
            continue
        try:
            geom = shape(polygon)
            if not geom.is_valid:
                geom = geom.buffer(0)
            sector_shapes.append((sector.get("id"), geom))
        except Exception:
            continue

    flows = {}
    sectors_of_pipe = {}
    for pipe in second_pipes:
        centroid = _pipe_centroid(pipe)
        if not centroid or not sector_shapes:
            continue
        point = Point(centroid)
        nearest = min(sector_shapes, key=lambda item: item[1].distance(point))
        flows[pipe.get("id")] = demand.get(nearest[0], 0.0)
        sectors_of_pipe[pipe.get("id")] = nearest[0]
    return flows, sectors_of_pipe


def _pressions(pressions_rows, station_head, required_pressure):
    """Available pressure at each zone inlet, and at the furthest emitter.

    The worst case is taken: the whole station head, less the head losses of
    the main and second pipes and of the zone pipe, less the elevation rise
    between the source and the zone.
    """
    elevations = [row["elevation"] for row in pressions_rows
                  if row.get("elevation") is not None]
    source_elevation = min(elevations) if elevations else 0.0
    rows = []
    for row in pressions_rows:
        loss = _f(row.get("head_loss_m"), 0.0)
        rise = (_f(row.get("elevation"), source_elevation) - source_elevation)
        pressure = station_head - loss - rise
        rows.append({
            **row,
            "available_mce": _round(pressure, 2),
            "required_mce": required_pressure,
            "ok": pressure >= required_pressure,
        })
    return rows


def _pump(flow_m3h, head_mce, settings, label):
    efficiency = _f(settings.get("pump_efficiency"), 0.70) or 0.70
    margin = _f(settings.get("motor_margin_pct"), 20) / 100.0
    motor_efficiency = _f(settings.get("motor_efficiency"), 0.75) or 0.75
    pump_kw = (flow_m3h * head_mce * 9.81 / 3600.0 / efficiency
               if flow_m3h and head_mce else None)
    motor_kw = (pump_kw * (1 + margin) / motor_efficiency
                if pump_kw is not None else None)
    return {
        "label": label,
        "flow_m3h": _round(flow_m3h, 2),
        "head_mce": _round(head_mce, 2),
        "efficiency": efficiency,
        "pump_kw": _round(pump_kw, 2) if pump_kw is not None else None,
        "motor_kw": _round(motor_kw, 2) if motor_kw is not None else None,
        "motor_cv": _round(motor_kw * 1.35962, 2) if motor_kw is not None else None,
    }


def _basin(source, settings):
    """Dimensions, volumes and membrane of a basin, from its own entries."""
    length = _f(source.get("length_m"), 0.0)
    width = _f(source.get("width_m"), 0.0)
    depth = _f(source.get("depth_m"), 0.0)
    talus = _f(settings.get("talus"), 1.0)
    revanche = _f(settings.get("revanche_m"), 0.30)

    area = _f(source.get("area_m2"), 0.0) or length * width
    volume = _f(source.get("volume_m3"), 0.0) or area * depth
    if not (length and width and depth):
        return {
            "length_m": length or None, "width_m": width or None,
            "depth_m": depth or None, "area_m2": _round(area, 0) or None,
            "volume_m3": _round(volume, 0) or None, "useful_m3": None,
            "membrane_m2": None, "talus": talus, "revanche_m": revanche,
            "complete": False,
        }

    # A trapezoidal basin: the top is wider than the bottom by talus x depth.
    top_length = length + 2 * talus * depth
    top_width = width + 2 * talus * depth
    slant = depth * math.sqrt(1 + talus ** 2)
    perimeter_bottom = 2 * (length + width)
    perimeter_top = 2 * (top_length + top_width)
    membrane = area + (perimeter_bottom + perimeter_top) / 2.0 * slant
    useful_volume = area * max(0.0, depth - revanche)
    return {
        "length_m": _round(length, 2),
        "width_m": _round(width, 2),
        "depth_m": _round(depth, 2),
        "area_m2": _round(area, 0),
        "volume_m3": _round(volume, 0),
        "useful_m3": _round(useful_volume, 0),
        "membrane_m2": _round(membrane, 0),
        "top_length_m": _round(top_length, 2),
        "top_width_m": _round(top_width, 2),
        "talus": talus,
        "revanche_m": revanche,
        "complete": True,
    }


def _water_balance(crops, net_area_ha, eto_days, rain_mm, flow_m3h,
                   pumping_hours):
    total_share = sum(_f(crop.get("share_pct"), 0.0) for crop in crops) / 100.0
    weighted_kc = 0.0
    if crops and total_share:
        weighted_kc = sum(crop["kc"] * _f(crop.get("share_pct"), 0.0) / 100.0
                          for crop in crops) / total_share

    months = []
    total_need = 0.0
    total_rain = 0.0
    for index in range(12):
        eto_month = eto_days[index] * MONTH_DAYS[index]
        rain = rain_mm[index]
        net_mm = max(0.0, eto_month * weighted_kc - rain)
        gross_mm = net_mm / APPLICATION_EFFICIENCY
        volume = gross_mm * 10.0 * net_area_ha
        hours = (volume / flow_m3h) if flow_m3h else None
        supply = flow_m3h * pumping_hours * MONTH_DAYS[index] if flow_m3h else 0.0
        total_need += volume
        total_rain += rain
        months.append({
            "month": MONTHS_SHORT_FR[index],
            "eto_mm_day": _round(eto_days[index], 2),
            "eto_mm": _round(eto_month, 1),
            "kc": _round(weighted_kc, 2),
            "rain_mm": _round(rain, 1),
            "net_mm": _round(net_mm, 1),
            "gross_mm": _round(gross_mm, 1),
            "volume_m3": _round(volume, 0),
            "hours": _round(hours, 1) if hours is not None else None,
            "supply_m3": _round(supply, 0),
            "balance_m3": _round(supply - volume, 0),
        })
    return {
        "months": months,
        "kc": _round(weighted_kc, 2),
        "total_need_m3": _round(total_need, 0),
        "total_rain_mm": _round(total_rain, 1),
        "flow_m3h": _round(flow_m3h, 2),
        "pumping_hours": pumping_hours,
    }


def _quote(project):
    from irrigation.estimation.routes import (
        _get_saved_estimation, compute_quantities, quote_totals,
    )

    lines, kpis = compute_quantities(project)
    estimation = _get_saved_estimation(project)
    priced = []
    for line in lines:
        price = _f((estimation.get("prices") or {}).get(line["key"]), 0.0)
        if not price:
            continue
        quantity = _f(line.get("quantity"), 0.0)
        priced.append({
            "label": line.get("name_en") or line.get("key"),
            "group": line.get("group") or "other",
            "unit": line.get("unit") or "unit",
            "quantity": _round(quantity, 2),
            "unit_price": price,
            "total": _round(quantity * price, 2),
        })
    for item in estimation.get("items") or []:
        quantity = _f(item.get("quantity"), 0.0)
        price = _f(item.get("unit_price"), 0.0)
        priced.append({
            "label": item.get("description") or "",
            "group": "extra",
            "unit": item.get("unit") or "unit",
            "quantity": _round(quantity, 2),
            "unit_price": price,
            "total": _round(quantity * price, 2),
        })
    totals = quote_totals(lines, estimation)
    return {
        "lines": priced,
        "currency": estimation.get("currency") or "MAD",
        "subtotal": _round(totals["subtotal"], 2),
        "discount_pct": _f(estimation.get("discount_pct"), 0.0),
        "discount_amount": _round(totals["discount_amount"], 2),
        "tax_pct": _f(estimation.get("tax_pct"), 0.0),
        "tax_amount": _round(totals["tax_amount"], 2),
        "total": _round(totals["total"], 2),
        "total_words": amount_in_words(totals["total"]),
        "kpis": kpis,
        "notes": estimation.get("notes") or "",
        "has_lines": bool(priced),
    }


# ---------------------------------------------------------------------------
# Amount in words (French, as printed under the quote)
# ---------------------------------------------------------------------------
_UNITS = ("zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept",
          "huit", "neuf", "dix", "onze", "douze", "treize", "quatorze",
          "quinze", "seize")
_TENS = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante",
         60: "soixante", 70: "soixante-dix", 80: "quatre-vingt",
         90: "quatre-vingt-dix"}


def _below_hundred(number):
    if number <= 16:
        return _UNITS[number]
    if number < 20:
        return "dix-" + _UNITS[number - 10]
    tens = (number // 10) * 10
    unit = number % 10
    if tens in (70, 90):
        return _TENS[tens] + ("-" + _UNITS[unit] if unit else "")
    if tens == 80:
        return _TENS[80] + ("-" + _UNITS[unit] if unit else "s")
    return _TENS[tens] + ("-" + _UNITS[unit] if unit else "")


def _below_thousand(number):
    hundreds, rest = divmod(number, 100)
    parts = []
    if hundreds:
        parts.append("cent" if hundreds == 1 else _UNITS[hundreds] + " cent"
                     + ("s" if not rest else ""))
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def _number_words(number):
    if number == 0:
        return "zéro"
    parts = []
    for value, label in ((1_000_000_000, "milliard"), (1_000_000, "million"),
                         (1_000, "mille")):
        count, number = divmod(number, value)
        if not count:
            continue
        if value == 1_000:
            parts.append("mille" if count == 1 else _below_thousand(count) + " mille")
        else:
            parts.append(_below_thousand(count) + f" {label}"
                         + ("s" if count > 1 else ""))
    if number:
        parts.append(_below_thousand(number))
    return " ".join(parts)


def amount_in_words(amount, currency="dirhams"):
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return ""
    whole = int(value)
    cents = int(round((value - whole) * 100))
    words = _number_words(whole) + " " + currency
    if cents:
        words += " et " + _number_words(cents) + " centimes"
    return words


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_context(project):
    """Everything the DPA template prints, read from the project entries."""
    settings = settings_for(project)
    spec = project.get("spec") or {}
    config = spec.get("config") or {}
    emitter_lph = _f(config.get("emitter_flow_lph"), 2.0)
    eto_days = settings["climate"]["eto_mm_day"]
    rain_mm = settings["climate"]["rain_mm"]
    water = settings["water"]

    try:
        lng, lat = get_project_centroid(project)
    except Exception:
        lng, lat = 0.0, 0.0

    sectors = _sectors(project)
    zones = _zones(sectors)
    rows = _rows(zones)
    trees = _trees(rows)

    boundary_area = _polygon_area(project.get("boundary"), lng, lat)
    sector_area = sum(_f(sector.get("area_m2"), 0.0) for sector in sectors)
    zone_area = sum(_f(zone.get("area_m2"), 0.0) for zone in zones)
    total_area_ha = _f(settings["exploitation"].get("total_area_ha"),
                       (boundary_area or sector_area) / 10000.0)
    net_area_ha = _f(settings["exploitation"].get("net_area_ha"),
                     (zone_area or sector_area) / 10000.0)

    row_spacing = _median([row.get("spacing_m") for row in rows]) or 0.0
    crops, peak_index = _crops(trees, net_area_ha, emitter_lph, eto_days, row_spacing)
    peak_bb = max((crop["bb_mm_day"] for crop in crops), default=0.0)

    posts = _posts(zones, emitter_lph, peak_bb, config)
    zone_flows = {post["zone_id"]: post["flow_m3h"] for post in posts}
    total_flow_m3h = sum(post["flow_m3h"] for post in posts)

    pipes = [pipe for pipe in ((project.get("network") or {}).get("pipes") or [])
             if isinstance(pipe, dict)]
    sector_of_zone = {zone.get("id"): zone.get("sector_id") for zone in zones}
    second_pipes = [pipe for pipe in pipes if pipe.get("type") == "second_pipe"]
    trunk_flows, trunk_sectors = _trunk_flows(second_pipes, sectors, sector_of_zone, posts)
    rampes = _rampes(zones, emitter_lph, water.get("dripline_diameter_mm"), lng, lat)
    zone_pipes = _pipe_rows([pipe for pipe in pipes if pipe.get("type") == "sub_pipe"],
                            zone_flows, total_flow_m3h, lng, lat)
    main_rows = _pipe_rows([pipe for pipe in pipes
                            if pipe.get("type") in ("main_pipe", "second_pipe")],
                           zone_flows, total_flow_m3h, lng, lat,
                           pipe_flows=trunk_flows, pipe_sectors=trunk_sectors)
    troncons = _pipe_rows(pipes, zone_flows, total_flow_m3h, lng, lat,
                          pipe_flows=trunk_flows, pipe_sectors=trunk_sectors)

    station_head = _f(water.get("station_head_mce"), 25.0)
    required = _f(water.get("min_pressure_mce"), 10.0)
    zone_loss = {pipe_row.get("zone_id"): _f(pipe_row.get("head_loss_m"), 0.0)
                 for pipe_row in zone_pipes}
    trunk_loss = {}
    for pipe_row in main_rows:
        if pipe_row.get("type") != "second_pipe":
            continue
        sector_id = pipe_row.get("sector_id")
        if sector_id is not None:
            trunk_loss[sector_id] = max(
                trunk_loss.get(sector_id, 0.0),
                _f(pipe_row.get("head_loss_m"), 0.0),
            )
    pressure_rows = _pressions(
        [{
            "zone": post["zone"],
            "sector": post["sector"],
            "flow_m3h": post["flow_m3h"],
            "elevation": post["elevation"],
            "head_loss_m": (zone_loss.get(post.get("zone_id"), 0.0)
                            + trunk_loss.get(sector_of_zone.get(post.get("zone_id")), 0.0)),
        } for post in posts],
        station_head,
        required,
    )

    sources = [source for source in (spec.get("water_sources") or project.get("water") or [])
               if isinstance(source, dict)]
    # The report lists the sources that feed the project; the points imported
    # from a KML outline would bury the wells and the basins on the page.
    report_sources = [source for source in sources
                      if str(source.get("type")).lower() in
                      ("well", "basin", "reservoir", "pump", "canal")]
    report_sources = report_sources or sources
    well = next((source for source in sources
                 if str(source.get("type")).lower() == "well"), None)
    basin_source = next((source for source in sources
                         if str(source.get("type")).lower() in ("basin", "reservoir")), None)

    source_flow = _f(water.get("flow_m3h"), 0.0)
    if not source_flow and well:
        source_flow = _f(well.get("flow_lpm"), 0.0) * 0.06
    # The pumps are sized on the design flow (the peak demand); the water
    # balance is only meaningful once the source flow is known.
    design_flow = source_flow or total_flow_m3h
    balance_flow = source_flow or total_flow_m3h
    source_flow_known = bool(source_flow)

    well_head = None
    pumping_level = None
    if well:
        water_level = _f(well.get("water_level_m"), 0.0)
        depth = _f(well.get("depth_m"), 0.0)
        pumping_level = water_level or (depth * 0.6 if depth else 0.0)
        if pumping_level:
            well_head = pumping_level + station_head
    well_pump = _pump(design_flow, well_head or 0.0, water,
                      "Groupe motopompe pour forage") if well_head else None

    basin = _basin(basin_source or {}, water)
    basin_head = None
    if basin_source and basin.get("depth_m"):
        basin_head = station_head + basin["depth_m"] + 0.5
    basin_pump = _pump(design_flow, basin_head or 0.0, water,
                       "Groupe motopompe pour bassin") if basin_head else None

    balance = _water_balance(crops, net_area_ha, eto_days, rain_mm,
                             balance_flow, _f(water.get("pumping_hours_day"), 20.0))
    balance["estimated_source"] = not source_flow_known
    quote = _quote(project)

    documents = {}
    for slot, label in DOC_SLOTS:
        path = (settings.get("documents") or {}).get(slot)
        documents[slot] = {"label": label, "path": path}

    owner = spec.get("owner") or {}
    owner_account = None
    if project.get("owner_user_id"):
        owner_account = mongo.db.users.find_one({"id": project.get("owner_user_id")})
    admin_account = None
    if project.get("admin_user_id"):
        admin_account = mongo.db.users.find_one({"id": project.get("admin_user_id")})

    company = dict(settings["company"])
    if not company.get("name") and admin_account:
        company["name"] = (admin_account.get("company") or {}).get("name") or ""

    client = (settings["exploitation"].get("client")
              or (owner_account or {}).get("full_name")
              or owner.get("name") or "")
    cin = (settings["exploitation"].get("cin")
           or (owner_account or {}).get("cin") or "")
    client_address = (settings["exploitation"].get("address")
                      or project.get("location") or "")

    report_date = settings["meta"].get("date") or ""
    if report_date and len(report_date) >= 7 and report_date[4] == "-":
        month_label = MONTHS_FR[int(report_date[5:7]) - 1] + " " + report_date[:4]
    elif report_date:
        month_label = report_date
    else:
        today = datetime.date.today()
        month_label = MONTHS_FR[today.month - 1] + " " + str(today.year)

    components = (project.get("network") or {}).get("components") or {}
    component_counts = {key: len(value or []) for key, value in components.items()}

    return {
        "project": project,
        "settings": settings,
        "company": company,
        "legal": settings["legal"],
        "meta": settings["meta"],
        "exploitation": settings["exploitation"],
        "client": client,
        "client_cin": cin,
        "client_address": client_address,
        "month_label": month_label,
        "sectors": sectors,
        "zones": zones,
        "trees": trees,
        "crops": crops,
        "peak_eto": _round(eto_days[peak_index], 2),
        "application_efficiency": APPLICATION_EFFICIENCY,
        "sources": report_sources,
        "posts": posts,
        "rampes": rampes,
        "zone_pipes": zone_pipes,
        "main_rows": main_rows,
        "troncons": troncons,
        "pressions": pressure_rows,
        "well": well,
        "pumping_level": _round(pumping_level, 2) if pumping_level else None,
        "basin_source": basin_source,
        "well_pump": well_pump,
        "basin_pump": basin_pump,
        "basin": basin,
        "balance": balance,
        "quote": quote,
        "documents": documents,
        "doc_slots": DOC_SLOTS,
        "component_counts": component_counts,
        "peak_month": MONTHS_FR[peak_index],
        "total_area_ha": _round(total_area_ha, 4),
        "net_area_ha": _round(net_area_ha, 4),
        "emitter_lph": emitter_lph,
        "station_head_mce": station_head,
        "min_pressure_mce": required,
        "total_flow_m3h": _round(total_flow_m3h, 2),
        "source_flow_m3h": _round(source_flow, 2),
        "source_flow_known": source_flow_known,
        "total_pipe_m": _round(sum(_f(row.get("length_m"), 0.0) for row in troncons), 1),
        "dripline_m": _round(sum(_f(row.get("length_m"), 0.0) for row in rampes), 1),
        "drippers": sum(post["drippers"] for post in posts),
        "trees_count": len(trees),
        "money": _round,
    }
