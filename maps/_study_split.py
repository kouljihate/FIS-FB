"""Temporary study: four distinct 10,000 m² sector layouts for
maps/sample-from-my-google-map.kml, rendered as one comparison PNG.

Layouts (each exactly 124 equal-area pieces):
  1. Elevation contour bands      — cuts follow the terrain contours
  2. Contour-aligned strips       — strip long axis parallel to the contour
  3. Fall-line strips             — strip long axis down the slope
  4. Axis grid (rows x cols)      — compact blocks on the land's long axis
                                    (this is what Auto Generate picks itself)
"""
import json
import math
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from shapely.geometry import Point as ShPoint

from app import app
from irrigation.extensions import mongo
from irrigation.engineering import regeneration as reg

NS = {'k': 'http://www.opengis.net/kml/2.2'}
KML = 'maps/sample-from-my-google-map.kml'
PID = 999001
TARGET = 10000
MIN_AREA = 9000
MAX_AREA = 10000


def parse_kml(path):
    root = ET.parse(path).getroot()
    boundary = None
    points = []
    for pm in root.iter('{http://www.opengis.net/kml/2.2}Placemark'):
        name_el = pm.find('k:name', NS)
        desc_el = pm.find('k:description', NS)
        name = (name_el.text or '').strip() if name_el is not None else ''
        desc = (desc_el.text or '').strip() if desc_el is not None else ''
        coords = pm.find('.//k:Polygon//k:coordinates', NS)
        if coords is not None:
            ring = [[float(t.split(',')[0]), float(t.split(',')[1])]
                    for t in (coords.text or '').split()]
            boundary = {'type': 'Polygon', 'coordinates': [ring]}
            continue
        pt = pm.find('k:Point/k:coordinates', NS)
        if pt is not None:
            parts = (pt.text or '').strip().split(',')
            points.append({'name': name, 'desc': desc,
                           'lng': float(parts[0]), 'lat': float(parts[1])})
    if boundary is None:
        raise SystemExit('no perimeter polygon found in KML')
    return boundary, points


def _largest(geom):
    if geom.geom_type == 'MultiPolygon':
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def exteriors(geom):
    """All exterior rings of a (Multi)Polygon."""
    if geom.geom_type == 'Polygon':
        return [list(geom.exterior.coords)]
    if geom.geom_type == 'MultiPolygon':
        return [list(g.exterior.coords) for g in geom.geoms]
    return []


def part_bearing(poly):
    """Major-axis bearing of a part, in degrees mod 180 (a line, not a vector)."""
    coords = np.array(_largest(poly).exterior.coords)
    c = coords - coords.mean(axis=0)
    _, vecs = np.linalg.eigh(c.T @ c)
    major = vecs[:, 1]
    return math.degrees(math.atan2(major[1], major[0])) % 180.0


def circ_mean180(angles):
    a = np.radians(np.asarray(angles, dtype=float) * 2.0)
    return (math.degrees(math.atan2(np.sin(a).mean(), np.cos(a).mean())) / 2.0) % 180.0


def compactness(poly):
    if poly.length <= 0:
        return 0.0
    return 4.0 * math.pi * poly.area / (poly.length ** 2)


def main():
    boundary, points = parse_kml(KML)
    ring = boundary['coordinates'][0]
    print('KML: %d boundary vertices, points=%s'
          % (len(ring), [(p['name'], p['desc']) for p in points]), flush=True)

    lng, lat = reg.get_project_centroid({'boundary': boundary})
    B = reg.projected_shape(boundary, lng, lat)
    area = B.area
    n = int(math.ceil(area / TARGET))
    print('Land area: %.1f m² (%.3f ha) -> %d sectors at %d m²'
          % (area, area / 10000, n, TARGET), flush=True)

    wpts = []
    for p in points:
        g, _ = reg.project_geometry({'type': 'Point',
                                     'coordinates': [p['lng'], p['lat']]}, lng, lat)
        wpts.append((p['name'], g.x, g.y))
    wxy = [(x, y) for _, x, y in wpts]
    w_pts = [ShPoint(x, y) for x, y in wxy]   # engine expects Point objects

    # -- scratch project (schema-compliant clone) -------------------------
    import copy as _copy
    from irrigation.storage import _empty_component_slots
    if mongo.db.projects.find_one({'id': PID}):
        mongo.db.projects.delete_one({'id': PID})
    template = mongo.db.projects.find_one({'id': 1})
    scratch = _copy.deepcopy(template)
    scratch.pop('_id', None)
    scratch.update({
        'id': PID,
        'name': 'Scratch 10k study',
        'location': KML,
        'description': 'temporary smart-split study',
        'boundary': boundary,
        'water_source': {'type': 'Point',
                         'coordinates': [points[0]['lng'], points[0]['lat']]},
        'water': [{'name': p['name'],
                   'type': 'well' if p['name'].upper().startswith('W') else 'source',
                   'lat': p['lat'], 'lng': p['lng'], 'flow_lpm': None, 'polygon': None,
                   'validated': False, 'notes': '', 'name_ar': '', 'id': i + 1}
                  for i, p in enumerate(points)],
        'sectors': [],
        'network': {'pipes': [], 'components': _empty_component_slots()},
        'reports': {'validations': []},
        'elevation_models': [],
        'status': 'Draft',
    })
    scratch['spec'] = _copy.deepcopy(template.get('spec') or {})
    scratch['spec']['rules'] = {'min_sector_area_m2': MIN_AREA,
                                'max_sector_area_m2': MAX_AREA}
    mongo.db.projects.insert_one(scratch)
    print('scratch project %d inserted' % PID, flush=True)

    try:
        with app.app_context():
            project = mongo.db.projects.find_one({'id': PID})
            em = reg.get_or_build_elevation_model(PID, project)
            if not em:
                raise SystemExit('no elevation model available')
            wse = em.get('water_source_elevation_m')
            stats_em = em.get('stats') or {}
            print('elevation model: %d samples, stats=%s, water_source=%.1f m'
                  % (len(em.get('samples') or []), json.dumps(stats_em), wse or -1),
                  flush=True)

            axis, elong = reg._principal_axis(B)
            terrain = reg._terrain_axes(em)
            print('principal axis %.1f° (elongation %.2f) | terrain %s'
                  % (axis, elong, terrain), flush=True)

            w_bal = reg._weights_for_preset('balanced', True)

            # -- probe the strip angle -> bearing mapping -----------------
            probe0 = reg._try_partition(B, n, 0.0, None, em, wse, w_bal,
                                        water_points=w_pts)
            probe1 = reg._try_partition(B, n, 15.0, None, em, wse, w_bal,
                                        water_points=w_pts)
            b0 = circ_mean180([part_bearing(p) for p in probe0['parts']])
            b1 = circ_mean180([part_bearing(p) for p in probe1['parts']])
            delta = (b1 - b0) % 180.0
            sign = 1.0 if abs(delta - 15.0) < abs(delta - 165.0) else -1.0
            print('strip probe: angle 0 -> %.1f°, angle 15 -> %.1f° (sign %+.0f)'
                  % (b0, b1, sign), flush=True)

            def angle_for(target_deg):
                # bearing(angle) = (b0 + sign*angle) mod 180
                return (sign * ((target_deg - b0) % 180.0)) % 180.0

            contour_deg = terrain['contour_deg']
            fallline_deg = terrain['fallline_deg'] % 180.0

            # -- the four candidate families ------------------------------
            candidates = {}

            r = reg._try_elevation_partition(B, n, em, None, wse, w_bal,
                                             water_points=w_pts)
            if r:
                candidates['1. Elevation contour bands'] = ('elevation_bands', r)

            a_c = angle_for(contour_deg)
            r = reg._try_partition(B, n, a_c, None, em, wse, w_bal, water_points=w_pts)
            if r:
                candidates['2. Contour-aligned strips'] = ('strips@%.1f' % a_c, r)

            a_f = angle_for(fallline_deg)
            r = reg._try_partition(B, n, a_f, None, em, wse, w_bal, water_points=w_pts)
            if r:
                candidates['3. Fall-line strips'] = ('strips@%.1f' % a_f, r)

            # the axis grid the engine itself would choose
            best_grid = None
            for rows, cols in reg._grid_shapes(n):
                if rows == 1 or cols == 1:
                    continue
                g = reg._try_grid_partition(B, rows, cols, axis or 0.0, None, em,
                                            wse, w_bal, water_points=w_pts)
                if g and (best_grid is None or g['score'] > best_grid['score']):
                    best_grid = g
            if best_grid:
                candidates['4. Axis grid (Auto Generate pick)'] = ('grid', best_grid)

            # -- score every layout under every priority -------------------
            priorities = ['balanced', 'water', 'elevation', 'area', 'compactness']
            report = {}
            rings_all = {}
            for label, (tag, res) in candidates.items():
                parts = res['parts']
                areas = [p.area for p in parts]
                all_emets = [reg.elevation_metrics_for_polygon(em, p) for p in parts]
                emets = [m for m in all_emets if m and m.get('sample_count')]
                means = [m['mean_elevation_m'] for m in emets]
                ranges = [m['range_m'] for m in emets]
                aspects = Counter()
                dists = []
                rings = []
                for p, m in zip(parts, all_emets):
                    asp = reg.aspect_for_polygon(em, p)
                    aspects[(asp or {}).get('compass') or '?'] += 1
                    c = p.centroid
                    dists.append(min(math.hypot(c.x - wx, c.y - wy) for wx, wy in wxy))
                    mean = (m or {}).get('mean_elevation_m')
                    for ring in exteriors(p):
                        rings.append((ring, mean))
                rings_all[label] = rings

                scores = {}
                for pr in priorities:
                    w = reg._weights_for_preset(pr, True)
                    scores[pr] = round(reg._score_partition(
                        parts, None, em, wse, w, water_points=w_pts), 4)

                report[label] = {
                    'tag': tag,
                    'n': len(parts),
                    'area_min': round(min(areas), 1),
                    'area_mean': round(sum(areas) / len(areas), 1),
                    'area_max': round(max(areas), 1),
                    'bearing_deg': round(circ_mean180([part_bearing(p)
                                                       for p in parts]), 1),
                    'compactness': round(sum(compactness(p) for p in parts)
                                         / len(parts), 4),
                    'elev_mean_in_sector_m': round(sum(ranges) / len(ranges), 2)
                    if ranges else None,
                    'elev_worst_in_sector_m': round(max(ranges), 2) if ranges else None,
                    'sector_mean_span_m': round(max(means) - min(means), 1)
                    if means else None,
                    'water_mean_m': round(sum(dists) / len(dists), 1),
                    'water_max_m': round(max(dists), 1),
                    'top_aspects': dict(aspects.most_common(3)),
                    'scores': scores,
                }
                print('%s -> n=%d bearing=%.1f° scores=%s'
                      % (label, len(parts), report[label]['bearing_deg'], scores),
                      flush=True)

            print('\n=== REPORT ===')
            print(json.dumps(report, indent=1))

            render(B, rings_all, report, wpts, contour_deg, fallline_deg,
                   axis, elong, terrain)
    finally:
        with app.app_context():
            res = mongo.db.projects.delete_one({'id': PID})
        print('scratch project removed:', res.deleted_count, flush=True)


def ramp(t):
    t = max(0.0, min(1.0, t))
    stops = [(0.00, (70, 130, 190)), (0.45, (120, 185, 110)),
             (0.75, (225, 205, 110)), (1.00, (185, 110, 65))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(int(round(a + (b - a) * f)) for a, b in zip(c0, c1))
    return stops[-1][1]


def render(B, rings_all, report, wpts, contour_deg, fallline_deg, axis, elong,
           terrain):
    from PIL import Image, ImageDraw, ImageFont

    PW, PH = 900, 700
    MARGIN = 78
    PAD = 26
    HEAD = 76                     # headline band above the first row of panels
    img = Image.new('RGB', (PW * 2, HEAD + (PH + MARGIN) * 2), 'white')
    draw = ImageDraw.Draw(img)
    try:
        font_b = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 24)
        font_s = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 16)
    except OSError:
        font_b = font_s = ImageFont.load_default()

    minx, miny, maxx, maxy = B.bounds
    scale = min((PW - 2 * PAD) / (maxx - minx), (PH - MARGIN - 2 * PAD) / (maxy - miny))
    lx = (PW - (maxx - minx) * scale) / 2
    ly = MARGIN + (PH - MARGIN - (maxy - miny) * scale) / 2

    all_means = [m for rings in rings_all.values() for _, m in rings if m is not None]
    lo, hi = (min(all_means), max(all_means)) if all_means else (0.0, 1.0)
    if hi - lo < 1e-6:
        hi = lo + 1.0

    labels = list(rings_all.keys())
    for idx, label in enumerate(labels):
        rings = rings_all[label]
        col, row = idx % 2, idx // 2
        panel = Image.new('RGB', (PW, PH + MARGIN), 'white')
        pdraw = ImageDraw.Draw(panel)

        def px(x, y):
            return (lx + (x - minx) * scale, ly + (maxy - y) * scale)

        for coords, mean in rings:
            pts = [px(x, y) for x, y in coords]
            fill = ramp((mean - lo) / (hi - lo)) if mean is not None else (205, 225, 200)
            pdraw.polygon(pts, fill=fill, outline=(110, 110, 110))
        pdraw.line([px(x, y) for x, y in B.exterior.coords],
                   fill=(20, 20, 20), width=3, joint='curve')
        for name, wx, wy in wpts:
            cx, cy = px(wx, wy)
            pdraw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8],
                          fill=(25, 60, 200), outline='white', width=2)
            pdraw.text((cx + 11, cy - 9), name, fill=(25, 60, 200), font=font_s)

        s = report.get(label, {})
        pdraw.text((16, 8), label, fill=(15, 60, 30), font=font_b)
        pdraw.text((16, 40),
                   ('%d sectors | %.0f m² each | strips/blocks at %.0f° | '
                    'in-sector relief %.1f m avg'
                    % (s.get('n', 0), s.get('area_mean') or 0,
                       s.get('bearing_deg') or 0,
                       s.get('elev_mean_in_sector_m') or 0)),
                   fill=(70, 70, 70), font=font_s)
        pdraw.text((16, 60),
                   ('compactness %.3f | nearest water avg %.0f m / max %.0f m'
                    % (s.get('compactness') or 0, s.get('water_mean_m') or 0,
                       s.get('water_max_m') or 0)),
                   fill=(70, 70, 70), font=font_s)
        img.paste(panel, (col * PW, HEAD + row * (PH + MARGIN)))

    # headline + colour bar
    head = ('Land 123.86 ha | 124 sectors x 10,000 m² | relief %.0f m | '
            'contour %.0f° | fall line %.0f° | long axis %.0f°'
            % (terrain.get('relief_m') or 0, contour_deg, fallline_deg, axis or 0))
    draw.text((PW - draw.textlength(head, font=font_b) / 2, 10), head,
              fill=(15, 45, 25), font=font_b)
    draw.text((PW - draw.textlength(
        'red = higher ground, blue = lower | blue dots = W1/W2 wells, S1/S2 sources',
        font=font_s) / 2, 44),
        'red = higher ground, blue = lower | blue dots = W1/W2 wells, S1/S2 sources',
        fill=(80, 80, 80), font=font_s)

    bar_w = 460
    bx = (PW * 2 - bar_w) // 2
    by = HEAD + (PH + MARGIN) * 2 - 30
    for i in range(bar_w):
        draw.line([(bx + i, by), (bx + i, by + 14)], fill=ramp(i / (bar_w - 1)))
    draw.text((bx - 170, by - 1), 'low %.0f m' % lo, fill='black', font=font_s)
    draw.text((bx + bar_w + 14, by - 1), '%.0f m high' % hi, fill='black', font=font_s)

    out = 'screenshots/sector-setups-10000m2.png'
    img.save(out)
    print('wrote', out, flush=True)


if __name__ == '__main__':
    main()
