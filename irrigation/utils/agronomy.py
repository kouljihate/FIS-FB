"""Irrigation agronomy: crop water demand and the water balance.

The classical FAO-56 water-balance chain is used:

    ETc = ET0 * Kc                              crop water use (mm/day)
    Pe  = effective rainfall (USDA SCS form)     usable rain (mm/day)
    NIR = max(0, ETc - Pe)                       net irrigation requirement
    GIR = NIR / efficiency                       gross irrigation requirement

followed by the soil-moisture reserves that decide how often to irrigate:

    TAW = 1000 * (FC - PWP) * Zr                 total available water (mm)
    RAW = TAW * p                                readily available water (mm)

Peak design flow then follows from the gross depth, the wetted area and the
number of hours the block may run per day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .units import application_rate_mm_per_h, depth_mm_to_volume_m3


@dataclass
class SoilProfile:
    """Volumetric water content at field capacity and wilting point (m3/m3)."""

    field_capacity: float = 0.30
    wilting_point: float = 0.14
    root_depth_m: float = 0.60
    depletion_fraction: float = 0.50

    @property
    def total_available_water_mm(self) -> float:
        return 1000.0 * (self.field_capacity - self.wilting_point) * self.root_depth_m

    @property
    def readily_available_water_mm(self) -> float:
        return self.total_available_water_mm * self.depletion_fraction


@dataclass
class WaterBalanceResult:
    etc_mm_per_day: float = 0.0
    effective_rain_mm_per_day: float = 0.0
    net_requirement_mm_per_day: float = 0.0
    gross_requirement_mm_per_day: float = 0.0
    interval_days: float = 0.0
    depth_per_event_mm: float = 0.0
    peak_flow_m3s: float = 0.0
    application_rate_mm_per_h: float = 0.0
    seasonal_volume_m3: float = 0.0
    notes: list[str] = field(default_factory=list)


def crop_evapotranspiration_mm_day(et0_mm_day: float, kc: float) -> float:
    """FAO-56 single crop coefficient form."""
    return max(0.0, et0_mm_day) * max(0.0, kc)


def effective_rainfall_mm(rain_mm: float) -> float:
    """USDA SCS effective-rainfall estimate for a monthly total.

    ``Pe = P * (125 - 0.2 P) / 125`` for ``P <= 250 mm``; above that the whole
    surplus is assumed to drain or run off, so ``Pe`` saturates.
    """
    if rain_mm <= 0:
        return 0.0
    if rain_mm > 250.0:
        return 100.0
    return rain_mm * (125.0 - 0.2 * rain_mm) / 125.0


def irrigation_efficiency(system: str = "drip") -> float:
    """Application efficiency used to gross up the net requirement."""
    return {
        "drip": 0.90,
        "micro_sprinkler": 0.85,
        "sprinkler": 0.75,
        "surface": 0.60,
        "furrow": 0.65,
    }.get(system, 0.85)


def water_balance(
    *,
    area_m2: float,
    et0_mm_per_day: float = 6.0,
    kc: float = 0.85,
    rainfall_mm_per_day: float = 0.0,
    system: str = "drip",
    hours_per_day: float = 16.0,
    soil: SoilProfile | None = None,
    season_days: int = 180,
) -> WaterBalanceResult:
    """Assemble the full design water balance for one block."""
    soil = soil or SoilProfile()
    result = WaterBalanceResult()

    if area_m2 <= 0:
        result.notes.append("No irrigated area defined yet.")
        return result
    if hours_per_day <= 0:
        hours_per_day = 16.0
        result.notes.append("Hours per day was not positive; assumed 16 h.")

    etc = crop_evapotranspiration_mm_day(et0_mm_per_day, kc)
    pe = effective_rainfall_mm(rainfall_mm_per_day)
    nir = max(0.0, etc - pe)
    efficiency = irrigation_efficiency(system)
    gir = nir / efficiency if efficiency > 0 else 0.0

    raw = soil.readily_available_water_mm
    interval = raw / nir if nir > 0 else 0.0
    depth_per_event = nir * max(interval, 1.0) if nir > 0 else 0.0

    # Peak design flow is the *daily* gross depth delivered inside the operating
    # window, not the whole soil-reserve event.  The event is spread over several
    # days by the irrigation interval, so using it here would overstate the flow
    # by roughly the interval length and every downstream pipe would be sized
    # against a flow the system never sees.
    daily_volume_m3 = depth_mm_to_volume_m3(gir, area_m2)
    peak_flow = daily_volume_m3 / (hours_per_day * 3600.0) if hours_per_day else 0.0

    result.etc_mm_per_day = round(etc, 3)
    result.effective_rain_mm_per_day = round(pe, 3)
    result.net_requirement_mm_per_day = round(nir, 3)
    result.gross_requirement_mm_per_day = round(gir, 3)
    result.interval_days = round(interval, 2)
    result.depth_per_event_mm = round(depth_per_event, 2)
    result.peak_flow_m3s = round(peak_flow, 6)
    result.application_rate_mm_per_h = round(
        application_rate_mm_per_h(peak_flow, area_m2), 3)
    result.seasonal_volume_m3 = round(
        depth_mm_to_volume_m3(gir * season_days, area_m2), 1)

    if nir <= 0:
        result.notes.append(
            "Effective rainfall covers the crop demand in this period; "
            "irrigation is supplementary only."
        )
    if interval and interval < 1.0:
        result.notes.append(
            "Required interval is under one day - split the block into more "
            "irrigation shifts or reduce the wetted area per valve."
        )
    if soil.readily_available_water_mm <= 0:
        result.notes.append("Soil profile parameters are inconsistent.")

    result.notes.append(
        f"Peak design flow is the {round(gir, 2)} mm/day gross depth over "
        f"{round(hours_per_day, 1)} h of operation; the {round(depth_per_event, 1)} mm "
        "soil-reserve event is delivered over several days."
    )

    return result


def leaching_requirement_mm(nir_mm: float, ec_water_ds_m: float,
                            ec_threshold_ds_m: float = 1.7) -> tuple[float, float]:
    """Extra depth needed to keep salts out of the root zone.

    Uses the FAO-29 leaching requirement ``LR = ECw / (5 * ECe - ECw)`` where
    ``ECe`` is the salinity the crop tolerates (the threshold above which yield
    starts to decline).  Returns ``(extra_depth_mm, leaching_fraction)``; the
    fraction is capped at 0.5 because beyond that drainage, not irrigation,
    becomes the limiting factor.
    """
    if nir_mm <= 0 or ec_water_ds_m <= 0 or ec_threshold_ds_m <= 0:
        return 0.0, 0.0
    if ec_water_ds_m <= ec_threshold_ds_m:
        return 0.0, 0.0

    denominator = 5.0 * ec_threshold_ds_m - ec_water_ds_m
    lf = 0.5 if denominator <= 0 else ec_water_ds_m / denominator
    lf = max(0.05, min(lf, 0.5))
    extra = nir_mm * lf / max(1.0 - lf, 1e-6)
    return round(extra, 3), round(lf, 4)


def pump_duty_point(peak_flow_m3s: float, total_head_m: float,
                    efficiency: float = 0.65) -> dict[str, float]:
    """Hydraulic power and the pump curve duty point.

    ``P = rho * g * Q * H / eta`` in watts.
    """
    from .units import G, RHO_WATER

    if peak_flow_m3s <= 0 or total_head_m <= 0:
        return {"power_kw": 0.0, "power_hp": 0.0, "efficiency": efficiency}
    efficiency = min(max(efficiency, 0.05), 0.9)
    power_w = RHO_WATER * G * peak_flow_m3s * total_head_m / efficiency
    return {
        "power_kw": round(power_w / 1000.0, 3),
        "power_hp": round(power_w / 745.7, 3),
        "efficiency": efficiency,
        "flow_m3h": round(peak_flow_m3s * 3600.0, 3),
        "head_m": round(total_head_m, 3),
    }


def shift_count(peak_flow_m3s: float, available_flow_m3s: float) -> int:
    """How many sequential shifts a block needs to fit the available supply."""
    if peak_flow_m3s <= 0 or available_flow_m3s <= 0:
        return 1
    import math

    return max(1, math.ceil(peak_flow_m3s / available_flow_m3s))


def wetted_area_m2(area_m2: float, wetted_percent: float) -> float:
    return area_m2 * max(0.0, min(wetted_percent, 100.0)) / 100.0


def total_dynamic_head_m(*, static_lift_m: float, friction_loss_m: float,
                         minor_loss_m: float = 0.0,
                         outlet_pressure_m: float = 0.0,
                         suction_loss_m: float = 0.0) -> float:
    """Total dynamic head a pump must deliver at the duty point."""
    return (max(0.0, static_lift_m) + max(0.0, friction_loss_m)
            + max(0.0, minor_loss_m) + max(0.0, outlet_pressure_m)
            + max(0.0, suction_loss_m))


def seasonal_irrigation_hours(seasonal_volume_m3: float,
                              flow_m3s: float) -> float:
    if flow_m3s <= 0:
        return 0.0
    return seasonal_volume_m3 / (flow_m3s * 3600.0)


def crop_water_use_series(kc_by_month: Sequence[float],
                          et0_by_month: Sequence[float]) -> list[float]:
    """ETc per month for charting."""
    return [round(crop_evapotranspiration_mm_day(e, k), 3)
            for k, e in zip(kc_by_month, et0_by_month)]
