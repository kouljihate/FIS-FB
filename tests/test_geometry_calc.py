"""Tests for the geometry engine.

The projected cases use a 100 m x 100 m square expressed in metre coordinates, so
expected areas and perimeters are exact.  The geographic cases assert against a
tolerance because they go through a real UTM projection.
"""

from __future__ import annotations

import math

import pytest

from irrigation.utils import geometry_calc as gc

SQUARE = [(0, 0), (100, 0), (100, 100), (0, 100)]
TRIANGLE = [(0, 0), (100, 0), (0, 100)]


def test_area_and_perimeter_metric_square():
    assert gc.area_m2(SQUARE) == pytest.approx(10_000.0, rel=1e-9)
    assert gc.area_ha(SQUARE) == pytest.approx(1.0, rel=1e-9)
    assert gc.perimeter_m(SQUARE) == pytest.approx(400.0, rel=1e-9)


def test_area_triangle():
    assert gc.area_m2(TRIANGLE) == pytest.approx(5_000.0, rel=1e-9)


def test_centroid_and_bounds():
    assert gc.centroid(SQUARE) == pytest.approx((50.0, 50.0))
    assert gc.bounds(SQUARE) == (0.0, 0.0, 100.0, 100.0)


def test_repeated_closing_vertex_is_dropped():
    closed = SQUARE + [SQUARE[0]]
    assert len(gc.as_points(closed)) == 4
    assert gc.area_m2(closed) == pytest.approx(10_000.0, rel=1e-9)


def test_geographic_square_area_is_about_right():
    # Near 36.72 N, 0.0011 deg of longitude is about 98 m and 0.0011 deg of
    # latitude about 122 m, so the projected area should be close to 12 000 m2.
    ring = [(8.1301, 36.7213), (8.1312, 36.7213), (8.1312, 36.7202), (8.1301, 36.7202)]
    assert gc.is_geographic(ring)
    area = gc.area_m2(ring)
    assert 11_000 < area < 13_000, area
    assert gc.describe(ring)["geographic"] is True


def test_describe_reports_validity():
    info = gc.describe(SQUARE)
    assert info["vertices"] == 4
    assert info["area_ha"] == pytest.approx(1.0, rel=1e-6)
    assert info["valid"] is True


def test_point_in_polygon():
    assert gc.point_in_polygon(SQUARE, (50, 50)) is True
    assert gc.point_in_polygon(SQUARE, (150, 50)) is False


def test_split_polygon_halves_the_area():
    parts = gc.split_polygon(SQUARE, mode="x", ratio=0.5)
    assert len(parts) == 2
    areas = sorted(gc.area_m2(part) for part in parts)
    assert areas[0] == pytest.approx(5_000.0, rel=1e-3)
    assert areas[1] == pytest.approx(5_000.0, rel=1e-3)
    assert sum(areas) == pytest.approx(10_000.0, rel=1e-3)


def test_split_respects_ratio():
    parts = gc.split_polygon(SQUARE, mode="x", ratio=0.25)
    areas = sorted(gc.area_m2(part) for part in parts)
    assert areas[0] == pytest.approx(2_500.0, rel=1e-2)


def test_split_into_n_keeps_total_area():
    parts = gc.split_into_n(SQUARE, 4)
    assert len(parts) == 4
    # Derived parts must be measured with the parent's CRS flag, never by shape
    # detection - see test_detection_alone_is_ambiguous_for_small_metric_polygons.
    assert sum(gc.area_m2(p, geographic=False) for p in parts) == pytest.approx(
        10_000.0, rel=1e-3)


def test_merge_polygons_unions_adjacent_squares():
    left = [(0, 0), (50, 0), (50, 100), (0, 100)]
    right = [(50, 0), (100, 0), (100, 100), (50, 100)]
    merged = gc.merge_polygons(left, right)
    assert gc.area_m2(merged) == pytest.approx(10_000.0, rel=1e-3)


def test_strip_rows_count_follows_spacing():
    strips = gc.strip_rows(SQUARE, 10.0)
    assert len(strips) == 10
    assert sum(gc.area_m2(s, geographic=False) for s in strips) == pytest.approx(
        10_000.0, rel=1e-2)


def test_centerline_points_stay_inside_and_are_spaced():
    # A 100 m x 2 m strip is metric, so the flag must be explicit: its raw
    # coordinates are numerically indistinguishable from degrees.
    row = [(0, 0), (100, 0), (100, 2), (0, 2)]
    points = gc.centerline_points(row, 10.0, geographic=False)
    assert len(points) == 10
    for x, y in points:
        assert 0 <= x <= 100 and 0 <= y <= 2
    xs = sorted(x for x, _ in points)
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    assert all(gap == pytest.approx(10.0, abs=1e-6) for gap in gaps)


def test_compactness_of_square_is_pi_over_4():
    # compactness rounds to 6 decimal places, hence the absolute tolerance.
    assert gc.compactness(SQUARE) == pytest.approx(math.pi / 4, abs=1e-6)


def test_detection_alone_is_ambiguous_for_small_metric_polygons():
    """Documents why the geographic flag is explicit rather than inferred.

    Every coordinate of a 100 m x 2 m strip falls inside +/-180 / +/-90, so
    shape-based detection calls it lon/lat and the area is then wrong by about
    ten orders of magnitude.  This is exactly the trap that derived sub-polygons
    used to fall into.
    """
    strip = [(0, 0), (100, 0), (100, 2), (0, 2)]
    assert gc.is_geographic(strip) is True
    assert gc.area_m2(strip, geographic=False) == pytest.approx(200.0, rel=1e-9)
    assert gc.area_m2(strip, geographic=True) > 1e6


def test_split_into_n_gives_equal_areas_for_three_parts():
    """Regression: recursive halving used to give thirds of 50/25/25 %."""
    parts = gc.split_into_n(SQUARE, 3, mode="x")
    assert len(parts) == 3
    areas = [gc.area_m2(p, geographic=False) for p in parts]
    for area in areas:
        assert area == pytest.approx(10_000.0 / 3, rel=1e-3)
    assert sum(areas) == pytest.approx(10_000.0, rel=1e-6)


def test_split_into_n_is_equal_on_an_irregular_shape():
    parts = gc.split_into_n(TRIANGLE, 3, mode="x")
    assert len(parts) == 3
    for part in parts:
        assert gc.area_m2(part, geographic=False) == pytest.approx(
            5_000.0 / 3, rel=1e-2)


def test_split_into_n_five_equal_parts():
    parts = gc.split_into_n(SQUARE, 5, mode="y")
    assert len(parts) == 5
    for part in parts:
        assert gc.area_m2(part, geographic=False) == pytest.approx(2_000.0, rel=1e-3)


def test_derived_sub_polygons_keep_the_parent_crs():
    """Regression: splitting a metric field must not reinterpret the parts."""
    parts = gc.split_into_n(SQUARE, 2, mode="y")
    assert len(parts) == 2
    for part in parts:
        assert gc.area_m2(part, geographic=False) == pytest.approx(5_000.0, rel=1e-6)
    assert sum(gc.area_m2(p, geographic=False) for p in parts) == pytest.approx(
        10_000.0, rel=1e-6)


def test_strip_rows_keep_the_parent_crs():
    strips = gc.strip_rows(SQUARE, 25.0)
    assert len(strips) == 4
    assert sum(gc.area_m2(s, geographic=False) for s in strips) == pytest.approx(
        10_000.0, rel=1e-6)


def test_short_input_is_rejected_by_split():
    with pytest.raises(gc.GeometryError):
        gc.split_polygon([(0, 0), (1, 1)])
