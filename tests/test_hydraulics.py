"""Tests for the pipe hydraulics.

Hazen-Williams is checked against a hand-computed reference case, and the inverse
solvers are verified by round-tripping their own answer rather than by asserting a
magic number.
"""

from __future__ import annotations

import math

import pytest

from irrigation.utils import hydraulics as hy

Q_REF = 0.01      # m3/s, about 158 gpm
D_REF = 0.10      # m
L_REF = 100.0     # m
C_REF = 140.0


def test_cross_section_and_velocity():
    assert hy.cross_section_m2(0.1) == pytest.approx(math.pi * 0.01 / 4, rel=1e-12)
    assert hy.velocity_m_s(Q_REF, D_REF) == pytest.approx(1.27324, rel=1e-5)


def test_velocity_rejects_zero_diameter():
    with pytest.raises(ValueError):
        hy.velocity_m_s(Q_REF, 0.0)


def test_reynolds_number_is_turbulent_for_the_reference_case():
    re = hy.reynolds_number(Q_REF, D_REF)
    assert 100_000 < re < 160_000, re


def test_reynolds_number_is_laminar_for_a_trickle():
    # 0.02 m/s through a 50 mm pipe.
    flow = 0.02 * hy.cross_section_m2(0.05)
    assert hy.reynolds_number(flow, 0.05) < 2000


def test_hazen_williams_matches_hand_computation():
    # hf = 10.67 * L * Q^1.852 / (C^1.852 * D^4.871)
    expected = (10.67 * L_REF * Q_REF ** 1.852
                / (C_REF ** 1.852 * D_REF ** 4.871))
    # Independently evaluated SI form: 10.67 x 100 x 0.01^1.852 /
    # (140^1.852 x 0.1^4.871) = 1.6616 m over 100 m at 1.27 m/s.
    assert expected == pytest.approx(1.6616, abs=5e-4)
    assert hy.hazen_williams_headloss_m(Q_REF, L_REF, D_REF, C_REF) == pytest.approx(
        expected, rel=1e-12)


def test_hazen_williams_is_linear_in_length():
    short = hy.hazen_williams_headloss_m(Q_REF, 100, D_REF)
    long = hy.hazen_williams_headloss_m(Q_REF, 200, D_REF)
    assert long == pytest.approx(2 * short, rel=1e-12)


def test_hazen_williams_flow_inverts_headloss():
    loss = hy.hazen_williams_headloss_m(Q_REF, L_REF, D_REF, C_REF)
    recovered = hy.hazen_williams_flow_m3s(D_REF, loss, L_REF, C_REF)
    assert recovered == pytest.approx(Q_REF, rel=1e-9)


def test_hazen_williams_diameter_inverts_headloss():
    loss = hy.hazen_williams_headloss_m(Q_REF, L_REF, D_REF, C_REF)
    recovered = hy.hazen_williams_diameter_m(Q_REF, loss, L_REF, C_REF)
    assert recovered == pytest.approx(D_REF, rel=1e-9)


def test_hazen_williams_zero_flow_gives_zero_loss():
    assert hy.hazen_williams_headloss_m(0.0, L_REF, D_REF) == 0.0


def test_darcy_weisbach_laminar_uses_64_over_re():
    diameter = 0.05
    velocity = 0.02
    flow = velocity * hy.cross_section_m2(diameter)
    re = hy.reynolds_number(flow, diameter)
    assert re < 2000

    f = hy.friction_factor(re, 1.5e-6, diameter)
    assert f == pytest.approx(64.0 / re, rel=1e-12)

    expected = f * (1000.0 / diameter) * hy.velocity_head_m(velocity)
    assert hy.darcy_weisbach_headloss_m(flow, 1000.0, diameter) == pytest.approx(
        expected, rel=1e-9)


def test_darcy_weisbach_turbulent_factor_is_physical():
    diameter = 0.05
    flow = 0.004
    re = hy.reynolds_number(flow, diameter)
    f = hy.friction_factor(re, 1.5e-6, diameter)
    assert re > 4000
    assert 0.014 < f < 0.022, f
    loss = hy.darcy_weisbach_headloss_m(flow, 100.0, diameter)
    assert 5.0 < loss < 10.0, loss


def test_rougher_pipe_loses_more_head():
    flow, diameter, length = 0.004, 0.05, 100.0
    smooth = hy.darcy_weisbach_headloss_m(flow, length, diameter, roughness_m=1.5e-6)
    rough = hy.darcy_weisbach_headloss_m(flow, length, diameter, roughness_m=3.0e-4)
    assert rough > smooth


def test_minor_loss_scales_with_k():
    v = hy.velocity_m_s(Q_REF, D_REF)
    assert hy.minor_loss_m(Q_REF, D_REF, 0.0) == 0.0
    assert hy.minor_loss_m(Q_REF, D_REF, 1.0) == pytest.approx(
        hy.velocity_head_m(v), rel=1e-12)
    assert hy.minor_loss_m(Q_REF, D_REF, 4.0) == pytest.approx(
        4 * hy.velocity_head_m(v), rel=1e-12)


def test_christiansen_factor_limits():
    assert hy.christiansen_f(1) == 1.0
    assert hy.christiansen_f(2) == pytest.approx(0.63910, abs=1e-5)
    # 1/2.852 + 1/20 + sqrt(0.852)/600 = 0.350631 + 0.05 + 0.001538
    assert hy.christiansen_f(10) == pytest.approx(0.4021695, abs=1e-6)
    # Many outlets converge on 1/(m+1) = 1/2.852
    assert hy.christiansen_f(1000) == pytest.approx(1 / 2.852, abs=2e-3)


def test_christiansen_factor_decreases_with_outlet_count():
    values = [hy.christiansen_f(n) for n in (1, 2, 5, 10, 50)]
    assert values == sorted(values, reverse=True)


def test_christiansen_rejects_silly_exponent():
    with pytest.raises(ValueError):
        hy.christiansen_f(5, exponent=1.0)


def test_equivalent_length():
    assert hy.equivalent_length_m(0.0, 0.05) == 0.0
    assert hy.equivalent_length_m(2.0, 0.05, f=0.02) == pytest.approx(5.0)


def test_pressure_after_headloss_subtracts_rise():
    assert hy.pressure_after_headloss_m(20.0, 3.0) == pytest.approx(17.0)
    assert hy.pressure_after_headloss_m(20.0, 3.0, 5.0) == pytest.approx(12.0)


def test_worst_branch_picks_the_highest_loss_pipe():
    flow = 0.005
    branches = [(100.0, 0.09), (180.0, 0.063), (60.0, 0.032)]
    worst = hy.worst_branch(flow, branches)
    assert worst is not None
    losses = {round(hy.hazen_williams_headloss_m(flow, length, dia), 6)
              for length, dia in branches}
    assert worst["headloss_m"] == max(losses)
    assert worst["diameter_m"] == 0.032
    assert worst["velocity_m_s"] == pytest.approx(hy.velocity_m_s(flow, 0.032), rel=1e-4)


def test_worst_branch_ignores_unusable_pipes():
    assert hy.worst_branch(0.005, []) is None
    assert hy.worst_branch(0.005, [(0.0, 0.09), (100.0, 0.0)]) is None


def test_worst_branch_is_not_the_sum_of_all_pipes():
    """Regression: summing every pipe as if in series gave absurd heads.

    Two parallel secondaries each carry half the flow, so the path loss is the
    worst single branch, not twice it.
    """
    full = hy.worst_branch(0.005, [(120.0, 0.063)])["headloss_m"]
    halved = hy.worst_branch(0.0025, [(120.0, 0.063), (120.0, 0.063)])["headloss_m"]
    assert halved < full
    assert halved == pytest.approx(hy.hazen_williams_headloss_m(0.0025, 120.0, 0.063))


def test_recommended_velocity_band():
    assert hy.recommended_velocity_ok(1.2) is True
    assert hy.recommended_velocity_ok(0.2) is False
    assert hy.recommended_velocity_ok(3.0) is False
