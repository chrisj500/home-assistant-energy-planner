from __future__ import annotations

from statistics import median
from typing import Iterable


def learned_wall_energy_full_kwh(
    samples_kwh: Iterable[float],
    fallback_kwh: float,
) -> tuple[float, int]:
    """Return the median learned full-range wall energy or the configured fallback."""
    samples = [
        float(value)
        for value in samples_kwh
        if 5.0 <= float(value) <= 250.0
    ]
    samples = samples[-20:]
    if samples:
        return float(median(samples)), len(samples)
    return max(float(fallback_kwh), 0.0), 0


def infer_wall_energy_full_kwh(
    *,
    wall_energy_kwh: float,
    start_soc_pct: float | None,
    end_soc_pct: float | None,
    minimum_soc_delta_pct: float = 5.0,
) -> float | None:
    """Infer AC wall energy for a 0-100% EV SOC span from one charging session.

    A minimum SOC change limits quantization error from integer vehicle-SOC sensors.
    Implausible results are rejected rather than contaminating the learned median.
    """
    if start_soc_pct is None or end_soc_pct is None:
        return None
    energy = max(float(wall_energy_kwh), 0.0)
    delta = float(end_soc_pct) - float(start_soc_pct)
    if energy < 0.5 or delta < max(float(minimum_soc_delta_pct), 0.1):
        return None
    inferred = energy * 100.0 / delta
    if not 5.0 <= inferred <= 250.0:
        return None
    return inferred


def residual_after_ev_charge(
    *,
    headroom_shortfall_kwh: float,
    capacity_export_kwh: float,
    ev_solar_energy_kwh: float,
    charge_efficiency: float,
) -> tuple[float, float]:
    """Return remaining stored-headroom shortfall and AC capacity export.

    Solar sent directly to the EV both consumes otherwise-surplus AC energy and
    preserves stationary-battery headroom equal to that AC energy times the
    battery's AC-to-stored charge efficiency.
    """
    solar = max(float(ev_solar_energy_kwh), 0.0)
    efficiency = min(max(float(charge_efficiency), 0.0), 1.0)
    preserved_headroom = solar * efficiency
    return (
        max(float(headroom_shortfall_kwh) - preserved_headroom, 0.0),
        max(float(capacity_export_kwh) - solar, 0.0),
    )
