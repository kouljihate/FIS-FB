"""Tests for the agronomy and water-balance helpers."""

from __future__ import annotations

import pytest

from irrigation.utils import agronomy as ag


def test_crop_evapotranspiration():
    assert ag.crop_evapotranspiration_mm_day(6.0, 0.85) == pytest.approx(5.1)
    assert ag.crop_evapotranspiration_mm_day(6.0, 0.0) == 0.0
    assert ag.crop_evapotranspiration_mm_day(-2.0, 0.9) == 0.0


def test_effective_rainfall_curve():
    assert ag.effective_rainfall_mm(0.0) == 0.0
    assert ag.effective_rainfall_mm(100.0) == pytest.approx(84.0)
    assert ag.effective_rainfall_mm(300.0) == pytest.approx(100.0)
    # Monotonic up to the 250 mm cap.
    values = [ag.effective_rainfall_mm(p) for p in (0, 50, 100, 200, 250)]
    assert values == sorted(values)


def test_irrigation_efficiency_by_system():
    assert ag.irrigation_efficiency("drip") == pytest.approx(0.90)
    assert ag.irrigation_efficiency("sprinkler") == pytest.approx(0.75)
    assert ag.irrigation_efficiency("unknown-system") == pytest.approx(0.85)


def test_soil_water_reserves():
    soil = ag.SoilProfile()  # FC .30, PWP .14, Zr .60, p .50
    assert soil.total_available_water_mm == pytest.approx(96.0)
    assert soil.readily_available_water_mm == pytest.approx(48.0)


def test_water_balance_reference_case():
    result = ag.water_balance(
        area_m2=10_000.0, et0_mm_per_day=6.0, kc=0.85,
        rainfall_mm_per_day=0.0, system="drip", hours_per_day=16.0,
        season_days=180,
    )
    assert result.etc_mm_per_day == pytest.approx(5.1)
    assert result.effective_rain_mm_per_day == 0.0
    assert result.net_requirement_mm_per_day == pytest.approx(5.1)
    assert result.gross_requirement_mm_per_day == pytest.approx(5.1 / 0.9, abs=1e-3)
    # RAW = 48 mm at 5.1 mm/day -> 9.41 day interval, 48 mm per event
    assert result.interval_days == pytest.approx(9.41, abs=0.01)
    assert result.depth_per_event_mm == pytest.approx(48.0, abs=0.1)
    # Peak flow is the DAILY gross depth (5.667 mm/day) over 1 ha in 16 h:
    # 56.667 m3 / 16 h = 3.54 m3/h.  Using the 48 mm reserve event instead would
    # have overstated it by roughly the 9.4 day interval.
    assert result.peak_flow_m3s * 3600 == pytest.approx(3.5417, abs=0.01)
    assert result.application_rate_mm_per_h == pytest.approx(0.3542, abs=0.01)
    assert result.seasonal_volume_m3 == pytest.approx(10_200.0, abs=20)
    assert any("Peak design flow" in note for note in result.notes)


def test_rainfall_reduces_the_requirement():
    dry = ag.water_balance(area_m2=10_000, rainfall_mm_per_day=0.0)
    wet = ag.water_balance(area_m2=10_000, rainfall_mm_per_day=2.0)
    assert wet.net_requirement_mm_per_day < dry.net_requirement_mm_per_day


def test_sufficient_rain_notes_supplementary_irrigation():
    result = ag.water_balance(area_m2=10_000, et0_mm_per_day=2.0, kc=0.5,
                              rainfall_mm_per_day=10.0)
    assert result.net_requirement_mm_per_day == 0.0
    assert any("rainfall covers" in note for note in result.notes)


def test_water_balance_without_area_notes_the_problem():
    result = ag.water_balance(area_m2=0.0)
    assert result.peak_flow_m3s == 0.0
    assert result.notes


def test_leaching_requirement_below_threshold_is_zero():
    assert ag.leaching_requirement_mm(5.1, ec_water_ds_m=1.0) == (0.0, 0.0)


def test_leaching_requirement_above_threshold():
    extra, fraction = ag.leaching_requirement_mm(5.1, ec_water_ds_m=2.0,
                                                 ec_threshold_ds_m=1.7)
    # LR = 2 / (5*1.7 - 2) = 0.30769 ; extra = 5.1 * LR/(1-LR)
    assert fraction == pytest.approx(0.3077, abs=1e-4)
    assert extra == pytest.approx(5.1 * 0.30769 / (1 - 0.30769), abs=0.01)


def test_leaching_fraction_is_capped():
    _, fraction = ag.leaching_requirement_mm(5.1, ec_water_ds_m=20.0)
    assert fraction <= 0.5


def test_peak_flow_scales_with_the_operating_window():
    short = ag.water_balance(area_m2=10_000, hours_per_day=8)
    long = ag.water_balance(area_m2=10_000, hours_per_day=16)
    assert short.peak_flow_m3s == pytest.approx(2 * long.peak_flow_m3s, rel=1e-6)


def test_peak_flow_is_independent_of_the_soil_reserve():
    """A deeper soil lengthens the interval but must not raise the design flow."""
    shallow = ag.water_balance(area_m2=10_000, soil=ag.SoilProfile(root_depth_m=0.3))
    deep = ag.water_balance(area_m2=10_000, soil=ag.SoilProfile(root_depth_m=1.2))
    assert deep.interval_days > shallow.interval_days
    assert deep.peak_flow_m3s == pytest.approx(shallow.peak_flow_m3s, rel=1e-9)


def test_pump_duty_point():
    duty = ag.pump_duty_point(0.008333, 40.0, efficiency=0.65)
    assert duty["power_kw"] == pytest.approx(5.02, abs=0.05)
    assert duty["power_hp"] == pytest.approx(6.73, abs=0.06)
    assert duty["flow_m3h"] == pytest.approx(30.0, abs=0.05)


def test_pump_duty_point_of_zero_flow():
    assert ag.pump_duty_point(0.0, 0.0)["power_kw"] == 0.0


def test_total_dynamic_head_sums_components():
    assert ag.total_dynamic_head_m(static_lift_m=25, friction_loss_m=5,
                                   minor_loss_m=1, outlet_pressure_m=10,
                                   suction_loss_m=2) == pytest.approx(43.0)
    # Negative inputs are clamped rather than reducing the head.
    assert ag.total_dynamic_head_m(static_lift_m=-5, friction_loss_m=10) == pytest.approx(10.0)


def test_shift_count_rounds_up():
    assert ag.shift_count(0.010, 0.004) == 3
    assert ag.shift_count(0.004, 0.004) == 1
    assert ag.shift_count(0.010, 0.0) == 1


def test_seasonal_hours():
    assert ag.seasonal_irrigation_hours(36_000.0, 0.001) == pytest.approx(10_000.0)
    assert ag.seasonal_irrigation_hours(1000.0, 0.0) == 0.0


def test_wetted_area_and_crop_series():
    assert ag.wetted_area_m2(10_000.0, 40.0) == pytest.approx(4_000.0)
    series = ag.crop_water_use_series([0.8, 0.9], [5.0, 6.0])
    assert series == [4.0, 5.4]
