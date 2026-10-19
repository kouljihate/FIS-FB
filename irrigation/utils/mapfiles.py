"""Read the map file a project is linked to and extract its land boundary.

A project cannot be created without one of these files, because the boundary it
draws *is* the land the whole design is measured against.

Supported formats
    * ``.kml``  - Google Earth / survey export, geographic lon/lat degrees.
    * ``.kmz``  - the same KML inside a zip archive.
    * ``.dxf``  - AutoCAD drawing exchange, taken as already-projected metres.

Only the standard library is used, so no extra dependency travels with the app.

The **largest closed outline in the file** becomes the boundary.  A survey export
usually carries the parcel plus annotation or neighbouring outlines, and the
parcel is the largest enclosed area; the alternative - taking the first ring we
meet - would silently pick a title block or a stray marker.

KML/KMZ leave the coordinates in degrees (``geographic`` is true); a DXF drawing
is a metric plan (``geographic`` is false), which is exactly what
:mod:`irrigation.utils.geometry_calc` needs to avoid mis-reading metres as
degrees.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

Point = tuple[float, float]

#: Extension -> internal kind.
KINDS = {"kml": "kml", "kmz": "kmz", "dxf": "dxf"}

#: Value for the ``accept`` attribute of the upload input.
ACCEPT = ".kml,.kmz,.dxf"

#: Human-readable list used in messages.
FORMATS = "KML, KMZ, DXF"


class MapFileError(ValueError):
    """Raised when a map file cannot be read or holds no usable outline."""


def detect_kind(filename: str | None) -> str | None:
    """Return the format of ``filename``, or ``None`` when it is not supported."""
    name = (filename or "").strip().lower()
    if "." not in name:
        return None
    return KINDS.get(name.rsplit(".", 1)[1])


def extract_boundary(kind: str, data: bytes) -> tuple[list[Point], bool]:
    """Return ``(boundary, geographic)`` for a map file's largest outline.

    ``geographic`` is true when the coordinates are lon/lat degrees.
    """
    if kind == "kml":
        rings, geographic = _kml_rings(data), True
    elif kind == "kmz":
        rings, geographic = _kml_rings(_kmz_member(data)), True
    elif kind == "dxf":
        rings, geographic = _dxf_rings(data), False
    else:
        raise MapFileError(f"unsupported map format: {kind}")

    ring = _largest_ring(rings)
    if ring is None:
        raise MapFileError(
            f"the file has no closed outline to use as the land boundary ({FORMATS})"
        )
    return ring, geographic


# --- shared helpers ----------------------------------------------------------

def _local(tag: Any) -> str:
    """Element tag without its ``{namespace}`` prefix."""
    return str(tag).rsplit("}", 1)[-1]


def _point(x: Any, y: Any, what: str) -> Point:
    try:
        return (float(x), float(y))
    except (TypeError, ValueError) as exc:
        raise MapFileError(f"{what} is not numeric: {x!r}, {y!r}") from exc


def _dedupe(points: list[Point]) -> list[Point]:
    """Drop a repeated closing vertex, as KML/DXF rings repeat the first point."""
    if len(points) > 2 and points[0] == points[-1]:
        points = points[:-1]
    return points


def _ring_area(points: list[Point]) -> float:
    """Absolute shoelace area; used only to rank candidate outlines."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _largest_ring(rings: list[list[Point]]) -> list[Point] | None:
    usable = [r for r in rings if len(r) >= 3 and _ring_area(r) > 0]
    if not usable:
        return None
    return max(usable, key=_ring_area)


# --- KML / KMZ ---------------------------------------------------------------

def _kmz_member(data: bytes) -> bytes:
    """Return the KML payload inside a KMZ archive."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise MapFileError("the KMZ archive is not a readable zip file") from exc

    with archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".kml")]
        if not names:
            raise MapFileError("the KMZ archive contains no KML document")
        # Prefer the conventional root ``doc.kml``, then the shallowest file: a
        # KMZ may embed several KML documents and the parcel lives in the top one.
        names.sort(key=lambda n: (n.lower().rsplit("/", 1)[-1] != "doc.kml",
                                  n.count("/"), len(n)))
        return archive.read(names[0])


def _kml_rings(data: bytes) -> list[list[Point]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise MapFileError(f"the KML is not valid XML ({exc})") from exc

    rings: list[list[Point]] = []
    for element in root.iter():
        if _local(element.tag) != "LinearRing":
            continue
        for child in element:
            if _local(child.tag) != "coordinates":
                continue
            points = _parse_kml_coordinates(child.text or "")
            if len(points) >= 3:
                rings.append(_dedupe(points))
    return rings


def _parse_kml_coordinates(text: str) -> list[Point]:
    """Parse KML ``lon,lat[,alt]`` tuples separated by whitespace."""
    points: list[Point] = []
    for token in text.replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        points.append(_point(parts[0], parts[1], "a KML coordinate"))
    return points


# --- DXF ---------------------------------------------------------------------

#: A drawing may be large; a boundary ring only needs its own vertices, so the
#: token stream is walked once and never held whole beyond that.
_MAX_TOKENS = 400_000


def _dxf_tokens(data: bytes):
    """Yield ``(group code, value)`` pairs from a DXF drawing.

    A DXF file is a flat sequence of lines: a numeric group code followed by its
    value.  Group 10 is an X coordinate, 20 the matching Y, and 0 starts a new
    entity.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # Pre-2007 DXF files carry an ANSI codepage; latin-1 never fails and the
        # numeric content we care about is ASCII either way.
        text = data.decode("latin-1")

    lines = text.splitlines()
    index = 0
    emitted = 0
    while index + 1 < len(lines) and emitted < _MAX_TOKENS:
        raw = lines[index].strip()
        index += 1
        if not re.fullmatch(r"-?\d+", raw):
            continue
        yield int(raw), lines[index].strip()
        index += 1
        emitted += 1


def _dxf_entities(data: bytes):
    """Yield ``(entity type, [(group code, value), ...])`` in file order."""
    entity: str | None = None
    codes: list[tuple[int, str]] = []
    for code, value in _dxf_tokens(data):
        if code == 0:
            if entity is not None:
                yield entity, codes
            entity = value.upper()
            codes = []
        elif entity is not None:
            codes.append((code, value))
    if entity is not None:
        yield entity, codes


def _dxf_rings(data: bytes) -> list[list[Point]]:
    """Every closed outline in the drawing.

    ``LWPOLYLINE`` carries its own vertices; the older ``POLYLINE`` style stores
    them in the following ``VERTEX`` entities and ends with ``SEQEND``, so those
    are accumulated across entities.
    """
    rings: list[list[Point]] = []
    polyline_points: list[Point] = []

    for entity, codes in _dxf_entities(data):
        points: list[Point] = []
        pending_x: float | None = None
        for code, value in codes:
            if code == 10:
                pending_x = _point(value, 0, "a DXF X coordinate")[0]
            elif code == 20 and pending_x is not None:
                points.append((pending_x, _point(0, value, "a DXF Y coordinate")[1]))
                pending_x = None

        if entity == "LWPOLYLINE":
            if len(points) >= 3:
                rings.append(_dedupe(points))
        elif entity == "VERTEX":
            polyline_points.extend(points)
        elif entity == "SEQEND":
            if len(polyline_points) >= 3:
                rings.append(_dedupe(polyline_points))
            polyline_points = []

    if len(polyline_points) >= 3:
        rings.append(_dedupe(polyline_points))
    return rings
