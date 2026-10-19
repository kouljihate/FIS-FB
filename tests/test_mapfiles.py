"""Unit tests for the map-file readers.

A project cannot be created without a map, so these parsers decide what a
project's land actually is.  The cases below pin the parts that matter: the
largest outline wins, KML is geographic while DXF is metric, and a file with
nothing closed is refused instead of producing an empty boundary.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from irrigation.utils import mapfiles

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>parcel</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>
      8.1301,36.7213,0 8.1322,36.7213,0 8.1322,36.7189,0 8.1301,36.7189,0 8.1301,36.7213,0
    </coordinates>
  </LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""


def _dxf(*entities: str) -> bytes:
    body = "\n".join(entities)
    return (
        "0\nSECTION\n2\nENTITIES\n"
        f"{body}\n"
        "0\nENDSEC\n0\nEOF\n"
    ).encode("ascii")


def _lwpolyline(points: list[tuple[float, float]]) -> str:
    rows = ["0", "LWPOLYLINE", "8", "0", "70", "1"]
    for x, y in points:
        rows += ["10", str(x), "20", str(y)]
    return "\n".join(rows)


def _polyline(points: list[tuple[float, float]]) -> str:
    rows = ["0", "POLYLINE", "8", "0", "70", "1"]
    for x, y in points:
        rows += ["0", "VERTEX", "8", "0", "10", str(x), "20", str(y)]
    rows += ["0", "SEQEND"]
    return "\n".join(rows)


# --- format detection --------------------------------------------------------

def test_detects_the_supported_extensions():
    assert mapfiles.detect_kind("land.kml") == "kml"
    assert mapfiles.detect_kind("LAND.KMZ") == "kmz"
    assert mapfiles.detect_kind("plot.dxf") == "dxf"


def test_ignores_anything_else():
    assert mapfiles.detect_kind("land.dwg") is None
    assert mapfiles.detect_kind("land") is None
    assert mapfiles.detect_kind(None) is None


def test_rejects_an_unknown_kind():
    with pytest.raises(mapfiles.MapFileError):
        mapfiles.extract_boundary("shapefile", b"")


# --- KML ---------------------------------------------------------------------

def test_kml_boundary_is_geographic_and_drops_the_repeated_vertex():
    boundary, geographic = mapfiles.extract_boundary("kml", KML.encode("utf-8"))
    assert geographic is True
    assert len(boundary) == 4
    assert boundary[0] == (8.1301, 36.7213)


def test_kml_keeps_the_largest_outline():
    """A survey export often carries annotation outlines besides the parcel."""
    small = ("<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
             "0,0,0 1,0,0 1,1,0 0,1,0</coordinates></LinearRing>"
             "</outerBoundaryIs></Polygon></Placemark>")
    big = ("<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
           "0,0,0 10,0,0 10,10,0 0,10,0</coordinates></LinearRing>"
           "</outerBoundaryIs></Polygon></Placemark>")
    data = f'<kml xmlns="http://www.opengis.net/kml/2.2">{small}{big}</kml>'.encode()
    boundary, _ = mapfiles.extract_boundary("kml", data)
    assert len(boundary) == 4
    assert max(x for x, _ in boundary) == 10.0


def test_kml_without_a_ring_is_refused():
    with pytest.raises(mapfiles.MapFileError):
        mapfiles.extract_boundary("kml", b"<kml><Document/></kml>")


def test_kml_that_is_not_xml_is_refused():
    with pytest.raises(mapfiles.MapFileError):
        mapfiles.extract_boundary("kml", b"<kml><unclosed>")


# --- KMZ ---------------------------------------------------------------------

def test_kmz_reads_the_kml_it_contains():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("doc.kml", KML)
    boundary, geographic = mapfiles.extract_boundary("kmz", buffer.getvalue())
    assert geographic is True
    assert len(boundary) == 4


def test_kmz_prefers_doc_kml_over_a_nested_document():
    smaller = ('<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>'
               "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
               "0,0,0 1,0,0 1,1,0 0,1,0</coordinates></LinearRing>"
               "</outerBoundaryIs></Polygon></Placemark></Document></kml>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("nested/other.kml", smaller)
        archive.writestr("doc.kml", KML)
    boundary, _ = mapfiles.extract_boundary("kmz", buffer.getvalue())
    assert max(x for x, _ in boundary) > 8.0


def test_kmz_without_a_kml_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "nothing here")
    with pytest.raises(mapfiles.MapFileError):
        mapfiles.extract_boundary("kmz", buffer.getvalue())


def test_kmz_that_is_not_a_zip_is_refused():
    with pytest.raises(mapfiles.MapFileError):
        mapfiles.extract_boundary("kmz", b"definitely not a zip")


# --- DXF ---------------------------------------------------------------------

def test_lwpolyline_becomes_a_metric_boundary():
    square = [(0.0, 0.0), (50.0, 0.0), (50.0, 30.0), (0.0, 30.0)]
    boundary, geographic = mapfiles.extract_boundary(
        "dxf", _dxf(_lwpolyline(square)))
    assert geographic is False
    assert len(boundary) == 4
    assert boundary[2] == (50.0, 30.0)


def test_polyline_vertices_become_a_metric_boundary():
    square = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
    boundary, geographic = mapfiles.extract_boundary(
        "dxf", _dxf(_polyline(square)))
    assert geographic is False
    assert len(boundary) == 4


def test_dxf_keeps_the_largest_outline():
    data = _dxf(_lwpolyline([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]),
                _lwpolyline([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]))
    boundary, _ = mapfiles.extract_boundary("dxf", data)
    assert max(x for x, _ in boundary) == 100.0


def test_dxf_without_an_outline_is_refused():
    with pytest.raises(mapfiles.MapFileError):
        mapfiles.extract_boundary("dxf", _dxf("0", "CIRCLE", "8", "0"))


def test_dxf_with_a_non_numeric_coordinate_is_refused():
    broken = "\n".join(["0", "LWPOLYLINE", "10", "abc", "20", "0"])
    with pytest.raises(mapfiles.MapFileError):
        mapfiles.extract_boundary("dxf", _dxf(broken))
