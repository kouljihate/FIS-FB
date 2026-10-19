"""Emitter discharge and irrigation uniformity.

A drip emitter is not a linear device: its flow follows ``q = k * p^x`` with an
exponent ``x`` around 0.5 for a plain turbulent dripper and near 0.0 for a
pressure-compensating one.  That exponent is what converts a small pressure drop
along a lateral into an uneven irrigation, and it is the reason the uniformity
metrics here are computed from pressures rather than from nominal flows.
"""

from __future__ import annotations

import math
from statistics import fmean, pstdev
from typing import Iterable, Sequence

#: Default discharge exponent for a non-compensating turbulent dripper.
DEFAULT_DISCHARGE_EXPONENT = 0.5

#: Suggested design targets (ASABE EP405 / EP458 practice).
TARGET_EU_MIN_PERCENT = 85.0
TARGET_DU_MIN_PERCENT = 75.0


def emitter_flow_lph(q_ref_lph: float, p_ref_m: float, p_working_m: float,
                     exponent: float = DEFAULT_DISCHARGE_EXPONENT) -> float:
    """Scale an emitter's rated flow to the working pressure.

    ``q = q_ref * (p / p_ref) ** x``.  Non-positive pressure means the emitter is
    not discharging at all, which is reported as zero rather than as a complex
    number.
    """
    if q_ref_lph <= 0 or p_ref_m <= 0:
        return 0.0
    if p_working_m <= 0:
        return 0.0
    return q_ref_lph * (p_working_m / p_ref_m) ** exponent


def emitter_flow_from_k(lph_per_m_pow_x: float, pressure_m: float,
                        exponent: float = DEFAULT_DISCHARGE_EXPONENT) -> float:
    """``q = k * p^x`` when the manufacturer quotes the coefficient directly."""
    if pressure_m <= 0:
        return 0.0
    return lph_per_m_pow_x * pressure_m ** exponent


def coefficient_k(q_ref_lph: float, p_ref_m: float,
                  exponent: float = DEFAULT_DISCHARGE_EXPONENT) -> float:
    """Back out ``k`` from one rated flow/pressure pair."""
    if p_ref_m <= 0:
        raise ValueError("reference pressure must be positive")
    return q_ref_lph / p_ref_m ** exponent


def manufacturer_variation(batch: Sequence[float]) -> float:
    """Coefficient of variation of a sample of emitter flows."""
    if len(batch) < 2:
        return 0.0
    mean = fmean(batch)
    if mean == 0:
        return 0.0
    return pstdev(batch) / mean


def distribution_uniformity_percent(flows: Iterable[float]) -> float:
    """Distribution uniformity (DU): mean of the lowest quarter over the mean.

    This is the field-measured metric from a catch-can or emitter-flow test.
    """
    values = sorted(float(f) for f in flows)
    if not values:
        return 0.0
    mean = fmean(values)
    if mean <= 0:
        return 0.0
    count = max(1, round(len(values) / 4))
    return 100.0 * fmean(values[:count]) / mean


def emission_uniformity_percent(flows: Iterable[float], *,
                                cv: float = 0.0,
                                emitters_per_plant: int = 1) -> float:
    """Design emission uniformity, the ASABE EP458 formulation.

    ``EU = 100 * (1 - 1.27 * CV / sqrt(n)) * (q_min / q_average)``

    where ``n`` is how many emitters feed one plant (a tree with four drippers
    is far more forgiving of a single poor emitter than one with a single
    dripper) and ``CV`` is the manufacturer's coefficient of variation.
    """
    values = [float(f) for f in flows]
    if not values:
        return 0.0
    mean = fmean(values)
    if mean <= 0:
        return 0.0
    q_min = min(values)
    n = max(int(emitters_per_plant), 1)
    statistical_term = max(0.0, 1.0 - 1.27 * cv / math.sqrt(n))
    return 100.0 * statistical_term * (q_min / mean)


def uniformity_report(flows: Iterable[float], *, cv: float = 0.0,
                      emitters_per_plant: int = 1) -> dict[str, float]:
    """Bundle the numbers a design report needs."""
    values = [float(f) for f in flows]
    if not values:
        return {}
    mean = fmean(values)
    return {
        "count": len(values),
        "q_min_lph": min(values),
        "q_max_lph": max(values),
        "q_mean_lph": mean,
        "q_std_lph": pstdev(values) if len(values) > 1 else 0.0,
        "variation_ratio": min(values) / mean if mean else 0.0,
        "du_percent": distribution_uniformity_percent(values),
        "eu_percent": emission_uniformity_percent(
            values, cv=cv, emitters_per_plant=emitters_per_plant),
    }


def emitter_count_per_plant(area_m2: float, tree_spacing_m: float,
                            row_spacing_m: float,
                            emitters_per_tree: int = 1) -> float:
    """Plants per hectare and the resulting number of emitters, for estimation."""
    if tree_spacing_m <= 0 or row_spacing_m <= 0:
        raise ValueError("spacings must be positive")
    plants = area_m2 / (tree_spacing_m * row_spacing_m)
    return plants * max(int(emitters_per_tree), 1)
