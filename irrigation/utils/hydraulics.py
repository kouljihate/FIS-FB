"""Steady-state pipe hydraulics in SI units.

Two friction models are provided because they answer different questions:

* **Hazen-Williams** is the industry convention for irrigation pipe sizing. Its
  coefficient ``C`` is tabulated per material and does not depend on Reynolds
  number, which makes it the model to use for PVC and polyethylene mains.
* **Darcy-Weisbach** is the physically based alternative needed when the fluid or
  the pipe differs from the norm — a laminar regime, a very rough old steel line,
  or a different viscosity.

Every function is pure and takes SI base units (m, m3/s, s).  Conversions live in
:mod:`irrigation.utils.units` so that no formula has to carry unit suffixes.
"""

from __future__ import annotations

import math
from typing import Iterable

from .units import G, NU_WATER_20C

#: Conventional Hazen-Williams roughness coefficients.
HAZEN_WILLIAMS_C = {
    "pvc": 140.0,
    "new_pvc": 150.0,
    "aged_pvc": 130.0,
    "pe": 140.0,          # polyethylene, common for laterals
    "hdpe": 140.0,
    "steel": 120.0,
    "galvanised": 120.0,
    "concrete": 120.0,
    "asbestos_cement": 140.0,
}

#: Absolute roughness (m) for Darcy-Weisbach.
PIPE_ROUGHNESS_M = {
    "pvc": 1.5e-6,
    "pe": 7.0e-6,
    "steel": 4.6e-5,
    "galvanised": 1.5e-4,
    "concrete": 3.0e-4,
    "asbestos_cement": 2.5e-5,
}


def cross_section_m2(diameter_m: float) -> float:
    if diameter_m <= 0:
        raise ValueError("diameter must be positive")
    return math.pi * diameter_m ** 2 / 4.0


def velocity_m_s(flow_m3s: float, diameter_m: float) -> float:
    """Mean axial velocity."""
    return flow_m3s / cross_section_m2(diameter_m)


def reynolds_number(flow_m3s: float, diameter_m: float,
                    nu: float = NU_WATER_20C) -> float:
    """Reynolds number; < 2000 laminar, > 4000 fully turbulent."""
    return velocity_m_s(flow_m3s, diameter_m) * diameter_m / nu


def velocity_head_m(velocity: float) -> float:
    return velocity ** 2 / (2.0 * G)


def friction_factor(reynolds: float, roughness_m: float, diameter_m: float) -> float:
    """Darcy friction factor.

    Laminar flow uses the exact ``64/Re``.  Turbulent flow uses the Swamee-Jain
    explicit approximation of Colebrook-White, accurate to about 1 % and far
    cheaper than iterating the implicit equation.
    """
    if reynolds <= 0:
        return 0.0
    if reynolds < 2000:
        return 64.0 / reynolds
    relative = roughness_m / diameter_m
    denominator = math.log10(relative / 3.7 + 5.74 / reynolds ** 0.9)
    if denominator == 0:
        return 0.0
    return 0.25 / denominator ** 2


def hazen_williams_headloss_m(flow_m3s: float, length_m: float, diameter_m: float,
                              c: float = 140.0) -> float:
    """Friction head loss by Hazen-Williams, in metres of water.

    ``hf = 10.67 * L * Q^1.852 / (C^1.852 * D^4.871)``
    """
    if flow_m3s <= 0 or length_m <= 0:
        return 0.0
    if diameter_m <= 0:
        raise ValueError("diameter must be positive")
    return (10.67 * length_m * flow_m3s ** 1.852
            / (c ** 1.852 * diameter_m ** 4.871))


def hazen_williams_flow_m3s(diameter_m: float, headloss_m: float, length_m: float,
                            c: float = 140.0) -> float:
    """Invert Hazen-Williams: the flow a given head loss will drive."""
    if headloss_m <= 0 or length_m <= 0:
        return 0.0
    return (headloss_m * c ** 1.852 * diameter_m ** 4.871
            / (10.67 * length_m)) ** (1.0 / 1.852)


def hazen_williams_diameter_m(flow_m3s: float, headloss_m: float, length_m: float,
                              c: float = 140.0) -> float:
    """Diameter that produces exactly ``headloss_m`` over ``length_m``."""
    if flow_m3s <= 0 or headloss_m <= 0 or length_m <= 0:
        return 0.0
    return (10.67 * length_m * flow_m3s ** 1.852
            / (c ** 1.852 * headloss_m)) ** (1.0 / 4.871)


def darcy_weisbach_headloss_m(flow_m3s: float, length_m: float, diameter_m: float,
                              roughness_m: float | None = None,
                              nu: float = NU_WATER_20C,
                              material: str = "pvc") -> float:
    """Friction head loss by Darcy-Weisbach, in metres of water."""
    if flow_m3s <= 0 or length_m <= 0:
        return 0.0
    if roughness_m is None:
        roughness_m = PIPE_ROUGHNESS_M.get(material, 1.5e-6)
    re = reynolds_number(flow_m3s, diameter_m, nu)
    f = friction_factor(re, roughness_m, diameter_m)
    return f * (length_m / diameter_m) * velocity_head_m(
        velocity_m_s(flow_m3s, diameter_m))


def minor_loss_m(flow_m3s: float, diameter_m: float, k: float = 0.0) -> float:
    """Local (fitting) head loss ``K * v^2 / 2g``."""
    if k == 0:
        return 0.0
    return k * velocity_head_m(velocity_m_s(flow_m3s, diameter_m))


def christiansen_f(n_outlets: int, exponent: float = 1.852) -> float:
    """Christiansen reduction factor for a multi-outlet pipe.

    A lateral with many evenly spaced outlets loses far less head than a line
    carrying the full flow to its end, because the flow tapers along it.  The
    factor multiplies the full-length, full-flow friction loss.

    ``F = 1/(m+1) + 1/(2N) + sqrt(m-1)/(6N^2)`` for ``m = 1.852``
    (the Hazen-Williams exponent).  For a single outlet the factor is 1.0.
    """
    n = max(int(n_outlets), 1)
    if n == 1:
        return 1.0
    if exponent <= 1:
        raise ValueError("exponent must exceed 1")
    return (1.0 / (exponent + 1.0)
            + 1.0 / (2.0 * n)
            + math.sqrt(exponent - 1.0) / (6.0 * n ** 2))


def equivalent_length_m(k_total: float, diameter_m: float,
                        f: float = 0.02) -> float:
    """Length of straight pipe whose friction equals the fittings' minor loss."""
    if k_total <= 0 or diameter_m <= 0 or f <= 0:
        return 0.0
    return k_total * diameter_m / f


def pressure_after_headloss_m(pressure_m: float, headloss_m: float,
                              elevation_change_m: float = 0.0) -> float:
    """Pressure at the downstream end of a reach.

    ``elevation_change_m`` is positive when the pipe *rises*, which removes
    pressure in addition to friction.
    """
    return pressure_m - headloss_m - elevation_change_m


def worst_branch(flow_m3s: float, pipes: Iterable[tuple[float, float]],
                 c: float = 140.0) -> dict[str, float] | None:
    """The most stressed pipe among parallel branches carrying ``flow_m3s``.

    Pipes at one level in the network are parallel routes, not a chain: two
    secondaries each carry half the block flow.  Friction along a path is set by
    the worst branch at each level, and adding every pipe in the project as if it
    were in series would overstate the loss by the number of pipes.

    ``pipes`` is an iterable of ``(length_m, diameter_m)`` pairs.
    """
    best: dict[str, float] | None = None
    for length_m, diameter_m in pipes:
        if length_m <= 0 or diameter_m <= 0:
            continue
        loss = hazen_williams_headloss_m(flow_m3s, length_m, diameter_m, c)
        if best is None or loss > best["headloss_m"]:
            best = {
                "headloss_m": round(loss, 6),
                "length_m": float(length_m),
                "diameter_m": float(diameter_m),
                "flow_m3s": float(flow_m3s),
                "velocity_m_s": round(velocity_m_s(flow_m3s, diameter_m), 4),
            }
    return best


def recommended_velocity_ok(velocity: float, *, low: float = 0.5,
                            high: float = 2.0) -> bool:
    """Whether a velocity sits inside the usual design band.

    Below ~0.5 m/s sediment settles and air is not carried out; above ~2 m/s
    water hammer and scour become a risk in plastic irrigation mains.
    """
    return low <= velocity <= high
