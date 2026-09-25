from __future__ import annotations

from datetime import datetime
from math import inf
from typing import Any

_MAX_SAMPLE_GAP_SECONDS = 300.0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(float(value), minimum), maximum)


def advance_counterfactual_ledger(
    ledger: dict[str, Any] | None,
    *,
    now: datetime,
    actual_stored_kwh: float,
    capacity_kwh: float,
    charge_limit_pct: float,
    charge_efficiency: float,
    ev_power_w: float,
    solar_surplus_w: float | None,
    max_sample_gap_seconds: float = _MAX_SAMPLE_GAP_SECONDS,
) -> dict[str, Any]:
    """Advance the no-discretionary-load battery counterfactual.

    The counterfactual follows observed stationary-battery movement, then adds
    back solar energy that was diverted into the EV. If the reconstructed bank
    would have exceeded its charge ceiling, the excess is counted as avoided
    AC export instead of being allowed to inflate the counterfactual SOC.

    Only intervals with continuous telemetry are integrated. A restart or long
    sampling gap still advances the counterfactual by the observed stationary
    battery delta, but it does not invent missing EV/solar energy.
    """
    capacity = max(float(capacity_kwh), 0.001)
    efficiency = _clamp(charge_efficiency, 0.0, 1.0)
    ceiling = capacity * _clamp(charge_limit_pct, 0.0, 100.0) / 100.0
    actual = _clamp(actual_stored_kwh, 0.0, capacity)
    ev_power = max(float(ev_power_w), 0.0)
    surplus = max(float(solar_surplus_w), 0.0) if solar_surplus_w is not None else 0.0
    day = now.date().isoformat()

    if not isinstance(ledger, dict) or ledger.get("date") != day:
        return {
            "date": day,
            "last_at": now.isoformat(),
            "last_actual_stored_kwh": actual,
            "last_ev_power_w": ev_power,
            "last_solar_surplus_w": surplus,
            "counterfactual_stored_kwh": min(actual, ceiling),
            "ev_wall_kwh": 0.0,
            "ev_solar_kwh": 0.0,
            "ev_solar_stored_equiv_kwh": 0.0,
            "avoided_export_kwh": 0.0,
            "integrated_seconds": 0.0,
        }

    previous = dict(ledger)
    try:
        last_at = datetime.fromisoformat(str(previous.get("last_at", "")))
    except ValueError:
        last_at = now

    cf_stored = _clamp(
        previous.get("counterfactual_stored_kwh", actual),
        0.0,
        ceiling,
    )
    previous_actual = _clamp(
        previous.get("last_actual_stored_kwh", actual),
        0.0,
        capacity,
    )
    actual_delta = actual - previous_actual
    elapsed_s = max((now - last_at).total_seconds(), 0.0)
    continuous = 0.0 < elapsed_s <= max(float(max_sample_gap_seconds), 0.0)

    # The no-EV battery experiences the same observed stationary-battery change.
    desired_cf = cf_stored + actual_delta
    overflow_stored = 0.0
    if continuous and max(
        float(previous.get("last_solar_surplus_w", 0.0) or 0.0),
        surplus,
    ) > 100.0:
        overflow_stored += max(desired_cf - ceiling, 0.0)
    cf_stored = _clamp(desired_cf, 0.0, ceiling)

    wall_delta = 0.0
    solar_delta = 0.0
    if continuous:
        previous_ev = max(float(previous.get("last_ev_power_w", ev_power) or 0.0), 0.0)
        previous_surplus = max(
            float(previous.get("last_solar_surplus_w", surplus) or 0.0),
            0.0,
        )
        wall_delta = (previous_ev + ev_power) / 2.0 * elapsed_s / 3_600_000.0
        previous_solar_ev = min(previous_ev, previous_surplus)
        current_solar_ev = min(ev_power, surplus)
        solar_delta = (
            (previous_solar_ev + current_solar_ev)
            / 2.0
            * elapsed_s
            / 3_600_000.0
        )

        diverted_stored = solar_delta * efficiency
        desired_cf = cf_stored + diverted_stored
        overflow_stored += max(desired_cf - ceiling, 0.0)
        cf_stored = _clamp(desired_cf, 0.0, ceiling)

    avoided_export_delta = (
        overflow_stored / efficiency if efficiency > 1e-9 else 0.0
    )

    return {
        "date": day,
        "last_at": now.isoformat(),
        "last_actual_stored_kwh": actual,
        "last_ev_power_w": ev_power,
        "last_solar_surplus_w": surplus,
        "counterfactual_stored_kwh": cf_stored,
        "ev_wall_kwh": max(float(previous.get("ev_wall_kwh", 0.0) or 0.0), 0.0)
        + wall_delta,
        "ev_solar_kwh": max(float(previous.get("ev_solar_kwh", 0.0) or 0.0), 0.0)
        + solar_delta,
        "ev_solar_stored_equiv_kwh": max(
            float(previous.get("ev_solar_stored_equiv_kwh", 0.0) or 0.0),
            0.0,
        )
        + solar_delta * efficiency,
        "avoided_export_kwh": max(
            float(previous.get("avoided_export_kwh", 0.0) or 0.0),
            0.0,
        )
        + avoided_export_delta,
        "integrated_seconds": max(
            float(previous.get("integrated_seconds", 0.0) or 0.0),
            0.0,
        )
        + (elapsed_s if continuous else 0.0),
    }


def counterfactual_bank_socs(
    *,
    actual_socs_pct: tuple[float, float, float],
    capacities_kwh: tuple[float, float, float],
    target_stored_kwh: float,
    charge_limit_pct: float,
) -> tuple[float, float, float]:
    """Water-fill the actual banks to the counterfactual aggregate energy."""
    capacities = tuple(max(float(value), 0.001) for value in capacities_kwh)
    limit = _clamp(charge_limit_pct, 0.0, 100.0)
    socs = [_clamp(value, 0.0, limit) for value in actual_socs_pct]
    stored = [cap * soc / 100.0 for cap, soc in zip(capacities, socs)]
    ceiling = [cap * limit / 100.0 for cap in capacities]
    target = _clamp(target_stored_kwh, sum(stored), sum(ceiling))
    remaining = target - sum(stored)

    while remaining > 1e-9:
        eligible = [index for index in range(3) if stored[index] < ceiling[index] - 1e-9]
        if not eligible:
            break
        current_socs = [100.0 * stored[index] / capacities[index] for index in eligible]
        minimum_soc = min(current_socs)
        lowest = [
            index
            for index in eligible
            if abs(100.0 * stored[index] / capacities[index] - minimum_soc) < 1e-9
        ]
        higher = [
            100.0 * stored[index] / capacities[index]
            for index in eligible
            if index not in lowest
        ]
        next_soc = min([limit, *higher]) if higher else limit
        group_capacity = sum(capacities[index] for index in lowest)
        needed = max(next_soc - minimum_soc, 0.0) / 100.0 * group_capacity
        if needed <= 1e-12:
            # Numerical tie: move one lowest bank directly toward its ceiling.
            index = lowest[0]
            accepted = min(remaining, ceiling[index] - stored[index])
            stored[index] += accepted
            remaining -= accepted
            continue
        accepted = min(remaining, needed)
        for index in lowest:
            stored[index] += accepted * capacities[index] / group_capacity
        remaining -= accepted

    return tuple(
        100.0 * stored[index] / capacities[index]
        for index in range(3)
    )


def live_capture_metrics(
    *,
    counterfactual_headroom_kwh: float,
    solar_surplus_w: float,
    remaining_daylight_hours: float,
    charge_efficiency: float,
) -> dict[str, float | bool]:
    """Describe saturation risk if the measured solar surplus persisted."""
    headroom = max(float(counterfactual_headroom_kwh), 0.0)
    surplus_w = max(float(solar_surplus_w), 0.0)
    remaining_h = max(float(remaining_daylight_hours), 0.0)
    efficiency = _clamp(charge_efficiency, 0.0, 1.0)
    stored_rate_kw = surplus_w / 1000.0 * efficiency
    fill_hours = headroom / stored_rate_kw if stored_rate_kw > 1e-9 else inf
    excess_ac_kwh = 0.0
    if surplus_w > 0.0 and efficiency > 1e-9:
        excess_ac_kwh = max(
            surplus_w / 1000.0 * remaining_h - headroom / efficiency,
            0.0,
        )
    return {
        "fill_hours": fill_hours,
        "remaining_daylight_hours": remaining_h,
        "risk": remaining_h > 0.0 and fill_hours <= remaining_h,
        "implied_excess_ac_kwh": excess_ac_kwh,
    }
