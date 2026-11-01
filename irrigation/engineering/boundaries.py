"""Keep the pipe paths on the boundary lines (Pipes -> Adjust).

The Main Pipe, the Second Pipes and the Zone Pipes are straight lines between
points that sit on the outlines of the land, the sectors and the zones.  Two
outline points joined by a straight line still cut across the inside of a
sector (or leave the land), so the map shows pipes crossing the blocks they are
supposed to border.

``adjust_pipes_to_boundaries`` re-routes the path of every pipe of one level
along the outline lines themselves:

* the two ends of the pipe never move, so the link to the water source, to the
  Main Pipe or to the zone a pipe feeds is kept;
* between those ends the path follows the boundary lines — the land outline,
  the sector outlines and, for the Zone Pipes, the zone outlines too;
* the route closest to the current alignment wins, so a pipe that already runs
  on a boundary keeps its shape instead of being moved to another edge;
* boundary rings that do not touch (a zone whose outline is inset inside its
  sector) are joined by one short link, so every pipe can still be routed.
"""

import heapq
import math
from collections import defaultdict

import pyproj
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, unary_union

from irrigation.engineering.piping import _BoundaryGraph
from irrigation.engineering.projection import (
    get_project_centroid, get_utm_crs, wgs84)
from irrigation.extensions import mongo

#: Pipe levels the Adjust tool knows how to re-route.
BOUNDARY_PIPE_TYPES = ('main_pipe', 'second_pipe', 'sub_pipe')

#: Which outlines a pipe level may follow.  The Main Pipe and the Second Pipe
#: stay on the land and sector boundaries (Rules MP-4 / SP-3); the Zone Pipes
#: also follow the zone outlines they serve (Rules ZP-2 / ZP-5).
BOUNDARY_SOURCES = {
    'main_pipe': ('land', 'sector'),
    'second_pipe': ('land', 'sector'),
    'sub_pipe': ('land', 'sector', 'zone'),
}

#: Vertices closer than this (metres) are the same boundary node.
SNAP_M = 0.5

#: Metres of extra path accepted per metre the boundary lies away from the
#: current pipe alignment.  Low enough to keep the pipe on its own border, high
#: enough to let it step onto a boundary that is a few metres away.
DEVIATION_WEIGHT = 1.5

#: Cost added to a link that joins two boundary rings: those links are a last
#: resort, never a shortcut between two edges of the same ring.
CONNECTOR_PENALTY = 1e6

#: Longest spacing (metres) between two nodes of a boundary segment.  Ring
#: vertices can be hundreds of metres apart; without this, a pipe would be
#: attached to the far end of a segment instead of the border right beside it.
NODE_SPACING_M = 10.0


def _polygon_rings(geometry):
    """Exterior rings (WGS84 coordinate lists) of a Polygon / MultiPolygon."""
    if not isinstance(geometry, dict):
        return []
    coordinates = geometry.get('coordinates') or []
    rings = []
    if geometry.get('type') == 'Polygon' and coordinates:
        rings.append(coordinates[0])
    elif geometry.get('type') == 'MultiPolygon':
        for polygon in coordinates:
            if polygon:
                rings.append(polygon[0])
    return [ring for ring in rings
            if isinstance(ring, (list, tuple)) and len(ring) >= 4]


def _boundary_rings(project, pipe_type):
    """``(kind, ring)`` pairs the pipe level is allowed to follow."""
    kinds = BOUNDARY_SOURCES.get(pipe_type, ('land', 'sector'))
    rings = []
    if 'land' in kinds:
        rings.extend(('land', ring) for ring in _polygon_rings(project.get('boundary')))
    if 'sector' in kinds:
        for sector in mongo.db.sectors.find({'project_id': project['id']}):
            rings.extend(('sector', ring)
                         for ring in _polygon_rings(sector.get('polygon')))
    if 'zone' in kinds:
        for zone in mongo.db.zones.find({'project_id': project['id']}):
            rings.extend(('zone', ring)
                         for ring in _polygon_rings(zone.get('polygon')))
    return rings


def _projected_lines(rings, transformer):
    """Outline lines in UTM metres, dissolved and merged where they meet.

    Sectors and zones share long stretches of their outlines, and a zone
    outline usually sits on its sector outline.  Taking the union first turns
    those shared stretches into one line with a junction wherever another
    outline crosses it, so the router can step from a zone ring onto the sector
    ring (and back) instead of walking around a detached loop.
    """
    lines = []
    for _kind, ring in rings:
        coordinates = []
        for point in ring:
            try:
                coordinates.append(transformer.transform(float(point[0]), float(point[1])))
            except (TypeError, ValueError, IndexError):
                continue
        if len(coordinates) < 3:
            continue
        lines.append(LineString(coordinates))
    if not lines:
        return []

    dissolved = unary_union(lines)
    if dissolved.is_empty:
        return []
    if dissolved.geom_type == 'LineString':
        dissolved = MultiLineString([dissolved])
    merged = linemerge(dissolved)
    if merged.geom_type == 'LineString':
        return [merged]
    if merged.geom_type in ('MultiLineString', 'GeometryCollection'):
        return [part for part in merged.geoms if part.geom_type == 'LineString']
    return []


def _densify(coordinates, step):
    """Add points every ``step`` metres so pipes attach close to their ends."""
    dense = [coordinates[0]]
    for start, end in zip(coordinates[:-1], coordinates[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        distance = math.hypot(dx, dy)
        if distance <= step:
            dense.append(end)
            continue
        pieces = int(math.ceil(distance / step))
        for index in range(1, pieces):
            ratio = index / float(pieces)
            dense.append((start[0] + dx * ratio, start[1] + dy * ratio))
        dense.append(end)
    return dense


def _build_graph(lines):
    """Boundary graph (UTM metres) whose edges are the outline segments."""
    graph = _BoundaryGraph(snap=SNAP_M)
    for line in lines:
        coordinates = _densify(list(line.coords), NODE_SPACING_M)
        node_ids = [graph.node_id(x, y, 0.0) for x, y in coordinates]
        compact = []
        for node_id in node_ids:
            if not compact or compact[-1] != node_id:
                compact.append(node_id)
        for index in range(len(compact) - 1):
            graph.add_edge(compact[index], compact[index + 1])
    return graph


def _follows_boundaries(line, boundaries):
    """True when the pipe already runs on the outline lines.

    The two links that reach the ends of the pipe are ignored (the ends do not
    move, even when they are off the border), so an adjusted pipe is reported
    as unchanged instead of being routed again on the next press.
    """
    coordinates = list(line.coords)
    tolerance = SNAP_M + 0.25
    if len(coordinates) <= 2:
        if line.length <= 0:
            return True
        probes = [line.interpolate(ratio, normalized=True)
                  for ratio in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)]
    elif len(coordinates) == 3:
        probes = [Point(coordinates[1])]
    else:
        inner = LineString(coordinates[1:-1])
        if inner.length <= 0:
            probes = [Point(coordinates[1])]
        else:
            samples = max(3, int(inner.length))
            probes = [inner.interpolate(index / samples, normalized=True)
                      for index in range(1, samples)]
    return all(boundaries.distance(point) <= tolerance for point in probes)


def _component_roots(graph):
    parent = list(range(len(graph.nodes)))

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for a, b, _length in graph.edges:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    components = defaultdict(list)
    for node in range(len(graph.nodes)):
        components[find(node)].append(node)
    return list(components.values())


def _connect_components(graph):
    """Join the separate boundary rings; returns the connector edge indices."""
    components = _component_roots(graph)
    connectors = set()
    if len(components) <= 1:
        return connectors

    components.sort(key=len, reverse=True)
    base = list(components[0])
    for component in components[1:]:
        best = None
        for node in component:
            node_x, node_y = graph.nodes[node][0], graph.nodes[node][1]
            for other in base:
                distance = math.hypot(graph.nodes[other][0] - node_x,
                                      graph.nodes[other][1] - node_y)
                if best is None or distance < best[0]:
                    best = (distance, node, other)
        if best is None:
            continue
        _distance, node, other = best
        before = len(graph.edges)
        graph.add_edge(node, other)
        if len(graph.edges) > before:
            connectors.add(len(graph.edges) - 1)
        base.extend(component)
    return connectors


def _nearest_node(graph, point):
    """Index of the boundary node closest to ``point`` (UTM metres)."""
    px, py = point[0], point[1]
    return min(range(len(graph.nodes)),
               key=lambda index: math.hypot(graph.nodes[index][0] - px,
                                            graph.nodes[index][1] - py))


def _edge_cost(graph, original, connectors):
    """Cost of one boundary edge: its length plus its distance to the pipe.

    The distance to the pipe keeps the new path where the pipe already runs,
    so Adjust repairs the route instead of moving the pipe to another border.
    """
    nodes = graph.nodes

    def cost(edge_index):
        a, b, length = graph.edges[edge_index]
        if edge_index in connectors:
            return length + CONNECTOR_PENALTY
        mid_x = (nodes[a][0] + nodes[b][0]) / 2.0
        mid_y = (nodes[a][1] + nodes[b][1]) / 2.0
        deviation = original.distance(Point(mid_x, mid_y))
        return length + DEVIATION_WEIGHT * deviation

    return cost


def _shortest_path(graph, source, target, edge_cost):
    """``(node_ids, edge_indices)`` from source to target, or ``None``."""
    if source == target:
        return [source], []

    distances = {source: 0.0}
    previous = {}
    queue = [(0.0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > distances.get(node, float('inf')) + 1e-9:
            continue
        if node == target:
            break
        for neighbour, edge_index in graph.adj[node]:
            candidate = distance + edge_cost(edge_index)
            if candidate < distances.get(neighbour, float('inf')) - 1e-9:
                distances[neighbour] = candidate
                previous[neighbour] = (node, edge_index)
                heapq.heappush(queue, (candidate, neighbour))

    if target not in distances:
        return None

    nodes = [target]
    edges = []
    current = target
    while current != source:
        step = previous.get(current)
        if step is None:
            return None
        node, edge_index = step
        edges.append(edge_index)
        nodes.append(node)
        current = node
    nodes.reverse()
    edges.reverse()
    return nodes, edges


def _line_length(coords):
    total = 0.0
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def _same_path(new_coords, old_coords):
    if len(new_coords) != len(old_coords):
        return False
    for new_point, old_point in zip(new_coords, old_coords):
        if abs(float(new_point[0]) - float(old_point[0])) > 1e-6:
            return False
        if abs(float(new_point[1]) - float(old_point[1])) > 1e-6:
            return False
    return True


def adjust_pipes_to_boundaries(project_id, project, pipe_type):
    """Re-route every pipe of ``pipe_type`` along the boundary lines.

    Returns a small report::

        {'total': n, 'adjusted': n, 'unchanged': n, 'skipped': n,
         'length_before_m': x, 'length_after_m': y}

    Raises ``ValueError`` when there is nothing to follow or nothing to route
    on; a pipe that cannot be routed keeps its geometry and is counted as
    skipped.
    """
    if pipe_type not in BOUNDARY_PIPE_TYPES:
        raise ValueError('Only the main, second and zone pipes can be adjusted / '
                         'يمكن ضبط الأنبوب الرئيسي والثانوي وأنابيب المناطق فقط')

    pipes = list(mongo.db.network_elements.find(
        {'project_id': project_id, 'type': pipe_type}))
    report = {'total': len(pipes), 'adjusted': 0, 'unchanged': 0, 'skipped': 0,
              'length_before_m': 0.0, 'length_after_m': 0.0}
    if not pipes:
        return report

    rings = _boundary_rings(project, pipe_type)
    if not rings:
        raise ValueError('No land, sector or zone boundary to follow — draw the '
                         'sectors first / لا توجد حدود للأرض أو القطاعات أو المناطق '
                         'لاتباعها — ارسم القطاعات أولا')

    try:
        lng, lat = get_project_centroid(project)
    except Exception:
        raise ValueError('The project has no boundary to project on / '
                         'المشروع لا يحتوي على حدود للإسقاط عليها')

    utm_crs = get_utm_crs(lng, lat)
    transformer = pyproj.Transformer.from_crs(wgs84, utm_crs, always_xy=True)
    inverse = pyproj.Transformer.from_crs(utm_crs, wgs84, always_xy=True)

    lines = _projected_lines(rings, transformer)
    graph = _build_graph(lines)
    if not graph.nodes:
        raise ValueError('The boundaries could not be projected / '
                         'تعذر إسقاط الحدود على الخريطة')
    boundaries = unary_union(lines)
    connectors = _connect_components(graph)

    for pipe in pipes:
        geometry = pipe.get('geometry') or {}
        coords = geometry.get('coordinates') or []
        if geometry.get('type') != 'LineString' or len(coords) < 2:
            report['skipped'] += 1
            continue
        try:
            utm = [transformer.transform(float(point[0]), float(point[1]))
                   for point in coords]
        except (TypeError, ValueError, IndexError):
            report['skipped'] += 1
            continue

        original = LineString(utm)
        if _follows_boundaries(original, boundaries):
            report['unchanged'] += 1
            continue

        start, end = utm[0], utm[-1]
        path = _shortest_path(graph, _nearest_node(graph, start),
                              _nearest_node(graph, end),
                              _edge_cost(graph, original, connectors))
        if not path:
            report['skipped'] += 1
            continue

        # The ends stay byte-for-byte what they were, and a boundary node that
        # nearly coincides with an end is not repeated: the paths are stored
        # with six decimals, so a re-run must find the same points again.
        new_utm = [start]
        for node in path[0]:
            point = (graph.nodes[node][0], graph.nodes[node][1])
            if math.hypot(point[0] - new_utm[-1][0],
                          point[1] - new_utm[-1][1]) > SNAP_M:
                new_utm.append(point)
        if len(new_utm) < 2 or math.hypot(
                end[0] - new_utm[-1][0], end[1] - new_utm[-1][1]) > SNAP_M:
            new_utm.append(end)

        wgs_coords = []
        for index, (x, y) in enumerate(new_utm):
            if index == 0:
                wgs_coords.append([float(coords[0][0]), float(coords[0][1])])
            elif index == len(new_utm) - 1:
                wgs_coords.append([float(coords[-1][0]), float(coords[-1][1])])
            else:
                lon, lat2 = inverse.transform(x, y)
                wgs_coords.append([round(lon, 6), round(lat2, 6)])

        if _same_path(wgs_coords, coords):
            report['unchanged'] += 1
            continue

        length_before = _line_length(utm)
        report['length_before_m'] += length_before
        report['length_after_m'] += _line_length(new_utm)

        mongo.db.network_elements.update_one(
            {'id': pipe['id'], 'project_id': project_id, 'type': pipe_type},
            {'$set': {
                'geometry': {'type': 'LineString', 'coordinates': wgs_coords},
                'properties.length_m': round(_line_length(new_utm), 2),
                'properties.adjusted_to_boundary': True,
            }}
        )
        report['adjusted'] += 1

    return report
