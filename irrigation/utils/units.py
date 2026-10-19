"""Physical constants and unit conversions.

Every internal computation is SI (metres, seconds, cubic metres).  The trade,
however, still designs in GPM, PSI, acres and inches, so the conversions live
here in one place instead of being sprinkled through the physics.
"""

from __future__ import annotations

import math

# --- constants ---------------------------------------------------------------

G: float = 9.80665                    # standard gravity, m/s^2
RHO_WATER: float = 998.2              # kg/m^3 at 20 C
NU_WATER_20C: float = 1.004e-6        # kinematic viscosity of water at 20 C

# --- length ------------------------------------------------------------------

M_PER_INCH: float = 0.0254
M_PER_FOOT: float = 0.3048


def inch_to_m(inch: float) -> float:
    return inch * M_PER_INCH


def m_to_inch(m: float) -> float:
    return m / M_PER_INCH


def mm_to_m(mm: float) -> float:
    return mm / 1000.0


def m_to_mm(m: float) -> float:
    return m * 1000.0


# --- area --------------------------------------------------------------------

M2_PER_ACRE: float = 4046.8564224
M2_PER_HECTARE: float = 10_000.0


def acre_to_m2(acre: float) -> float:
    return acre * M2_PER_ACRE


def m2_to_acre(m2: float) -> float:
    return m2 / M2_PER_ACRE


def hectare_to_m2(ha: float) -> float:
    return ha * M2_PER_HECTARE


def m2_to_hectare(m2: float) -> float:
    return m2 / M2_PER_HECTARE


# --- flow --------------------------------------------------------------------

M3S_PER_GPM: float = 6.30901964e-5
M3S_PER_LPH: float = 1.0 / 3_600_000.0


def gpm_to_m3s(gpm: float) -> float:
    return gpm * M3S_PER_GPM


def m3s_to_gpm(q: float) -> float:
    return q / M3S_PER_GPM


def lph_to_m3s(lph: float) -> float:
    return lph * M3S_PER_LPH


def m3s_to_lph(q: float) -> float:
    return q / M3S_PER_LPH


def m3h_to_m3s(q: float) -> float:
    return q / 3600.0


def m3s_to_m3h(q: float) -> float:
    return q * 3600.0


# --- pressure ----------------------------------------------------------------

M_HEAD_PER_PSI: float = 0.703249
M_HEAD_PER_BAR: float = 10.1972


def psi_to_m_head(psi: float) -> float:
    return psi * M_HEAD_PER_PSI


def m_head_to_psi(m: float) -> float:
    return m / M_HEAD_PER_PSI


def bar_to_m_head(bar: float) -> float:
    return bar * M_HEAD_PER_BAR


def m_head_to_bar(m: float) -> float:
    return m / M_HEAD_PER_BAR


def kpa_to_m_head(kpa: float) -> float:
    return kpa * 1000.0 / (RHO_WATER * G)


def m_head_to_kpa(m: float) -> float:
    return m * RHO_WATER * G / 1000.0


# --- application depth -------------------------------------------------------


def depth_mm_to_volume_m3(depth_mm: float, area_m2: float) -> float:
    return depth_mm / 1000.0 * area_m2


def volume_m3_to_depth_mm(volume_m3: float, area_m2: float) -> float:
    if area_m2 <= 0:
        raise ValueError("area must be positive")
    return volume_m3 / area_m2 * 1000.0


def application_rate_mm_per_h(q_m3s: float, area_m2: float) -> float:
    """Average application rate of a flow spread over an area."""
    if area_m2 <= 0:
        raise ValueError("area must be positive")
    return q_m3s / area_m2 * 3_600_000.0


# --- slope -------------------------------------------------------------------


def percent_slope_to_grade(percent: float) -> float:
    return percent / 100.0


def grade_to_percent_slope(grade: float) -> float:
    return grade * 100.0


def grade_to_angle_deg(grade: float) -> float:
    return math.degrees(math.atan(grade))


def angle_deg_to_grade(angle_deg: float) -> float:
    return math.tan(math.radians(angle_deg))
