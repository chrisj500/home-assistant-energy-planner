from __future__ import annotations

from datetime import datetime
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


def soc_observation_is_trusted(
    *,
    observed_at: datetime | None,
    session_started_at: datetime | None,
    age_minutes: float | None,
    max_prestart_age_minutes: float = 5.0,
    max_live_age_minutes: float = 30.0,
) -> bool:
    """Return whether an EV SOC observation is safe to use as a learning anchor.

    A reading observed after charging starts is accepted while it remains reasonably
    fresh. A reading from before charging started is accepted only when it was
    refreshed immediately before the session. This prevents stale app telemetry from
    being paired with wall energy that was delivered before the learner had a valid
    SOC anchor.
    """
    if observed_at is None or session_started_at is None or age_minutes is None:
        return False
    age = max(float(age_minutes), 0.0)
    if observed_at >= session_started_at:
        return age <= max(float(max_live_age_minutes), 0.0)
    prestart_age = max((session_started_at - observed_at).total_seconds() / 60.0, 0.0)
    limit = max(float(max_prestart_age_minutes), 0.0)
    return prestart_age <= limit and age <= limit


def infer_anchored_wall_energy_full_kwh(
    *,
    anchor_wall_energy_kwh: float | None,
    observed_wall_energy_kwh: float | None,
    anchor_soc_pct: float | None,
    observed_soc_pct: float | None,
    minimum_soc_delta_pct: float = 5.0,
) -> float | None:
    """Infer EV full-range wall energy only from energy delivered after a trusted anchor."""
    if anchor_wall_energy_kwh is None or observed_wall_energy_kwh is None:
        return None
    energy = max(float(observed_wall_energy_kwh) - float(anchor_wall_energy_kwh), 0.0)
    return infer_wall_energy_full_kwh(
        wall_energy_kwh=energy,
        start_soc_pct=anchor_soc_pct,
        end_soc_pct=observed_soc_pct,
        minimum_soc_delta_pct=minimum_soc_delta_pct,
    )


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
