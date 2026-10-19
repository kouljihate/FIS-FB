"""Tests for emitter discharge and irrigation uniformity."""

from __future__ import annotations

import pytest

from irrigation.utils import emitters as em


def test_rated_point_reproduces_the_catalogue_value():
    assert em.emitter_flow_lph(2.0, 10.0, 10.0) == pytest.approx(2.0)


def test_flow_follows_the_square_root_law():
    # Halving the pressure at x = 0.5 costs 1/sqrt(2) of the flow.
    assert em.emitter_flow_lph(2.0, 10.0, 5.0) == pytest.approx(2.0 / 2 ** 0.5)
    assert em.emitter_flow_lph(2.0, 10.0, 4.0) == pytest.approx(2.0 * 0.4 ** 0.5)


def test_pressure_compensating_emitter_is_flat():
    flat = em.emitter_flow_lph(2.0, 10.0, 12.0, exponent=0.0)
    assert flat == pytest.approx(2.0)
    assert em.emitter_flow_lph(2.0, 10.0, 6.0, exponent=0.0) == pytest.approx(2.0)


def test_no_pressure_means_no_flow():
    assert em.emitter_flow_lph(2.0, 10.0, 0.0) == 0.0
    assert em.emitter_flow_lph(2.0, 10.0, -3.0) == 0.0


def test_coefficient_k_round_trips():
    k = em.coefficient_k(2.0, 10.0)
    assert k == pytest.approx(2.0 / 10 ** 0.5)
    assert em.emitter_flow_from_k(k, 10.0) == pytest.approx(2.0)
    assert em.emitter_flow_from_k(k, 2.5) == pytest.approx(2.0 * 0.25 ** 0.5)


def test_manufacturer_variation():
    assert em.manufacturer_variation([1.0, 1.0, 1.0]) == pytest.approx(0.0)
    assert em.manufacturer_variation([0.8, 1.2]) == pytest.approx(0.2)
    assert em.manufacturer_variation([1.0]) == 0.0


def test_distribution_uniformity_uses_the_lowest_quarter():
    flows = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    # lowest quarter = [1, 2] -> mean 1.5, overall mean 4.5
    assert em.distribution_uniformity_percent(flows) == pytest.approx(100 * 1.5 / 4.5)


def test_distribution_uniformity_of_identical_emitters_is_100():
    assert em.distribution_uniformity_percent([2.0] * 8) == pytest.approx(100.0)


def test_emission_uniformity_without_variation_is_qmin_over_mean():
    # qmin = 8, mean = 9 -> 88.89 %
    assert em.emission_uniformity_percent([8.0, 10.0], cv=0.0) == pytest.approx(100 * 8 / 9)


def test_emission_uniformity_penalises_manufacturer_variation():
    # 1 - 1.27 * 0.05 / sqrt(1) = 0.9365, times qmin/qmean = 8/9
    value = em.emission_uniformity_percent([8.0, 10.0], cv=0.05, emitters_per_plant=1)
    assert value == pytest.approx(100 * (1 - 0.0635) * (8 / 9), abs=1e-6)


def test_more_emitters_per_plant_reduce_the_variation_penalty():
    one = em.emission_uniformity_percent([8.0, 10.0], cv=0.05, emitters_per_plant=1)
    four = em.emission_uniformity_percent([8.0, 10.0], cv=0.05, emitters_per_plant=4)
    assert four > one


def test_uniformity_report_contains_the_expected_keys():
    report = em.uniformity_report([1.8, 2.0, 2.2, 2.0], cv=0.03)
    for key in ("count", "q_min_lph", "q_max_lph", "q_mean_lph", "q_std_lph",
                "variation_ratio", "du_percent", "eu_percent"):
        assert key in report
    assert report["count"] == 4
    assert report["q_mean_lph"] == pytest.approx(2.0)
    assert report["variation_ratio"] == pytest.approx(0.9)


def test_uniformity_report_of_nothing_is_empty():
    assert em.uniformity_report([]) == {}


def test_emitter_count_per_plant():
    # 1 ha at 4 m x 5 m spacing = 500 plants, one emitter each.
    assert em.emitter_count_per_plant(10_000, 4.0, 5.0) == pytest.approx(500.0)
    assert em.emitter_count_per_plant(10_000, 4.0, 5.0, emitters_per_tree=4) == pytest.approx(2000.0)
