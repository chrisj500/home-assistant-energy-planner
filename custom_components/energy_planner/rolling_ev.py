from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Iterable

try:
    from .calibration import apply_stored_energy_drop
    from .forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh, power_at
    from .simulation import ControllerSettings, simulate_energy_flow
except ImportError:  # pragma: no cover - direct unit-test import
    from calibration import apply_stored_energy_drop
    from forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh, power_at
    from simulation import ControllerSettings, simulate_energy_flow


@dataclass(frozen=True, slots=True)
class DaylightWindow:
    day: date
    sunrise: datetime
    sunset: datetime


@dataclass(frozen=True, slots=True)
class RollingDayPlan:
    day: date
    start: datetime
    end: datetime
    start_soc_pct: float
    end_soc_pct: float
    solar_kwh: float
    stored_charge_kwh: float
    grid_import_kwh: float
    export_kwh: float
    capacity_export_kwh: float
    power_export_kwh: float
    required_headroom_kwh: float
    available_headroom_kwh: float
    headroom_margin_kwh: float
    headroom_shortfall_kwh: float
    starting_bank_socs_pct: tuple[float, float, float]
    ending_bank_socs_pct: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class EvChargeWindow:
    start: datetime
    end: datetime
    requested_energy_kwh: float
    solar_energy_kwh: float
    grid_energy_kwh: float
    preserved_stationary_headroom_kwh: float


def planning_base_load_w(
    recent_3h_w: float | None,
    recent_24h_w: float | None,
    fallback_w: float | None,
    *,
    recent_weight: float = 0.45,
) -> tuple[float | None, str]:
    """Return a stable non-EV planning load and an auditable source label."""
    weight = min(max(float(recent_weight), 0.0), 1.0)
    recent = float(recent_3h_w) if recent_3h_w is not None and recent_3h_w > 0 else None
    daily = float(recent_24h_w) if recent_24h_w is not None and recent_24h_w > 0 else None
    fallback = float(fallback_w) if fallback_w is not None and fallback_w >= 0 else None

    if recent is not None and daily is not None:
        return weight * recent + (1.0 - weight) * daily, "blend_3h_24h"
    if recent is not None:
        return recent, "recent_3h"
    if daily is not None:
        return daily, "recent_24h"
    if fallback is not None:
        return fallback, "instantaneous_fallback"
    return None, "unavailable"


def learned_charge_power_w(
    samples_w: Iterable[float],
    fallback_w: float,
) -> tuple[float, int]:
    """Use observed charging power when available, otherwise a configured fallback."""
    samples = [float(value) for value in samples_w if float(value) >= 500.0]
    if samples:
        return float(median(samples[-120:])), len(samples[-120:])
    return max(float(fallback_w), 0.0), 0


def ev_wall_energy_to_target_kwh(
    *,
    current_soc_pct: float | None,
    target_soc_pct: float,
    wall_kwh_full: float,
) -> float | None:
    """Estimate AC wall energy the EV can still accept before its configured target."""
    if current_soc_pct is None:
        return None
    current = min(max(float(current_soc_pct), 0.0), 100.0)
    target = min(max(float(target_soc_pct), 0.0), 100.0)
    full = max(float(wall_kwh_full), 0.0)
    if full <= 0:
        return None
    return max(target - current, 0.0) / 100.0 * full


def _stored_kwh(
    socs: tuple[float, float, float],
    capacities: tuple[float, float, float],
) -> float:
    return sum(capacity * soc / 100.0 for capacity, soc in zip(capacities, socs))


def _aggregate_soc(
    socs: tuple[float, float, float],
    capacities: tuple[float, float, float],
) -> float:
    return 100.0 * _stored_kwh(socs, capacities) / sum(capacities)


def simulate_rolling_days(
    *,
    points: list[IntervalPoint],
    reference: datetime,
    daylight_windows: list[DaylightWindow],
    initial_bank_socs_pct: tuple[float, float, float],
    bank_capacities_kwh: tuple[float, float, float],
    charge_limit_pct: float,
    reserve_pct: float,
    charge_efficiency: float,
    controller: ControllerSettings,
    average_load_kw: float,
    overnight_drop_kw: float,
    step_minutes: int = 5,
) -> list[RollingDayPlan]:
    """Simulate sequential solar days using the paid interval curve when present.

    The stationary battery remains protected from daylight discharge. Between
    solar windows, the empirical median overnight depletion rate is applied to
    the stored-energy state, bounded by the effective reserve floor.
    """
    if not points or not daylight_windows:
        return []

    capacities = tuple(max(float(value), 0.001) for value in bank_capacities_kwh)
    socs = tuple(min(max(float(value), 0.0), 100.0) for value in initial_bank_socs_pct)
    charge_limit = min(max(float(charge_limit_pct), 0.0), 100.0)
    reserve = min(max(float(reserve_pct), 0.0), 100.0)
    efficiency = min(max(float(charge_efficiency), 0.0), 1.0)
    load_kw = max(float(average_load_kw), 0.0)
    drop_kw = max(float(overnight_drop_kw), 0.0)
    cursor = reference
    results: list[RollingDayPlan] = []

    for window in daylight_windows:
        if window.sunset <= reference:
            continue

        if cursor < window.sunrise:
            hours = max((window.sunrise - cursor).total_seconds() / 3600.0, 0.0)
            socs = apply_stored_energy_drop(
                bank_socs_pct=socs,
                bank_capacities_kwh=capacities,
                drop_kwh=drop_kw * hours,
                reserve_pct=reserve,
            )

        start = max(window.sunrise, reference) if window.day == reference.date() else window.sunrise
        if start >= window.sunset:
            cursor = max(cursor, window.sunset)
            continue
        duration_h = (window.sunset - start).total_seconds() / 3600.0

        start_socs = socs
        start_stored = _stored_kwh(start_socs, capacities)
        charge_ceiling_kwh = sum(capacities) * charge_limit / 100.0
        available_headroom = max(charge_ceiling_kwh - start_stored, 0.0)

        def solar_curve(elapsed_h: float, *, _start=start) -> float:
            at = _start + timedelta(hours=max(float(elapsed_h), 0.0))
            return power_at(points, at) / 1000.0

        simulation = simulate_energy_flow(
            duration_h=duration_h,
            solar_power_kw=solar_curve,
            average_load_kw=load_kw,
            bank_socs_pct=start_socs,
            bank_capacities_kwh=capacities,
            charge_limit_pct=charge_limit,
            charge_efficiency=efficiency,
            controller=controller,
            step_minutes=step_minutes,
        )
        unlimited = simulate_energy_flow(
            duration_h=duration_h,
            solar_power_kw=solar_curve,
            average_load_kw=load_kw,
            bank_socs_pct=start_socs,
            bank_capacities_kwh=capacities,
            charge_limit_pct=charge_limit,
            charge_efficiency=efficiency,
            controller=controller,
            step_minutes=step_minutes,
            ignore_battery_capacity=True,
        )

        required_headroom = unlimited.solar_to_battery_ac_kwh * efficiency
        margin = available_headroom - required_headroom
        shortfall = max(-margin, 0.0)
        solar_kwh = integrate_interval_energy_kwh(
            points,
            start,
            window.sunset,
            step_minutes=step_minutes,
        )

        socs = simulation.ending_bank_socs_pct
        results.append(
            RollingDayPlan(
                day=window.day,
                start=start,
                end=window.sunset,
                start_soc_pct=_aggregate_soc(start_socs, capacities),
                end_soc_pct=simulation.aggregate_soc_pct,
                solar_kwh=solar_kwh,
                stored_charge_kwh=simulation.stored_charge_kwh,
                grid_import_kwh=simulation.predicted_grid_import_kwh,
                export_kwh=simulation.predicted_export_kwh,
                capacity_export_kwh=simulation.capacity_limited_export_kwh,
                power_export_kwh=simulation.power_limited_export_kwh,
                required_headroom_kwh=required_headroom,
                available_headroom_kwh=available_headroom,
                headroom_margin_kwh=margin,
                headroom_shortfall_kwh=shortfall,
                starting_bank_socs_pct=start_socs,
                ending_bank_socs_pct=socs,
            )
        )
        cursor = window.sunset

    return results


def first_headroom_risk(
    plans: list[RollingDayPlan],
    *,
    shortfall_threshold_kwh: float = 0.25,
    export_threshold_kwh: float = 0.25,
) -> RollingDayPlan | None:
    for plan in plans:
        if (
            plan.headroom_shortfall_kwh >= max(float(shortfall_threshold_kwh), 0.0)
            or plan.capacity_export_kwh >= max(float(export_threshold_kwh), 0.0)
        ):
            return plan
    return None


def _window_energy_split(
    *,
    points: list[IntervalPoint],
    start: datetime,
    end: datetime,
    base_load_kw: float,
    charge_power_kw: float,
    requested_energy_kwh: float,
    step_minutes: int,
) -> tuple[float, float]:
    cursor = start
    step = timedelta(minutes=max(int(step_minutes), 1))
    remaining = max(float(requested_energy_kwh), 0.0)
    solar_energy = 0.0
    grid_energy = 0.0
    while cursor < end and remaining > 1e-9:
        next_cursor = min(cursor + step, end)
        hours = (next_cursor - cursor).total_seconds() / 3600.0
        midpoint = cursor + (next_cursor - cursor) / 2
        interval_energy = min(charge_power_kw * hours, remaining)
        if interval_energy <= 0:
            break
        solar_kw = power_at(points, midpoint) / 1000.0
        surplus_kw = max(solar_kw - base_load_kw, 0.0)
        solar_fraction = min(max(surplus_kw / max(charge_power_kw, 0.001), 0.0), 1.0)
        from_solar = interval_energy * solar_fraction
        solar_energy += from_solar
        grid_energy += interval_energy - from_solar
        remaining -= interval_energy
        cursor = next_cursor
    return solar_energy, grid_energy


def choose_ev_charge_window(
    *,
    points: list[IntervalPoint],
    daylight_windows: list[DaylightWindow],
    earliest: datetime,
    latest: datetime,
    base_load_kw: float,
    charge_power_w: float,
    energy_kwh: float,
    charge_efficiency: float,
    step_minutes: int = 15,
) -> EvChargeWindow | None:
    """Choose the contiguous solar window that preserves the most battery headroom."""
    power_kw = max(float(charge_power_w), 0.0) / 1000.0
    requested = max(float(energy_kwh), 0.0)
    if not points or power_kw <= 0 or requested <= 0 or latest <= earliest:
        return None

    duration_h = requested / power_kw
    if duration_h <= 0:
        return None
    step = timedelta(minutes=max(int(step_minutes), 1))
    duration = timedelta(hours=duration_h)
    best: EvChargeWindow | None = None
    best_score: tuple[float, float, float] | None = None

    for window in daylight_windows:
        window_start = max(window.sunrise, earliest)
        window_end = min(window.sunset, latest)
        if window_end - window_start < duration:
            continue
        candidate = window_start
        while candidate + duration <= window_end + timedelta(seconds=1):
            end = candidate + duration
            solar_energy, grid_energy = _window_energy_split(
                points=points,
                start=candidate,
                end=end,
                base_load_kw=max(float(base_load_kw), 0.0),
                charge_power_kw=power_kw,
                requested_energy_kwh=requested,
                step_minutes=min(max(int(step_minutes), 1), 5),
            )
            preserved = solar_energy * min(max(float(charge_efficiency), 0.0), 1.0)
            score = (solar_energy, -grid_energy, candidate.timestamp())
            if best_score is None or score > best_score:
                best_score = score
                best = EvChargeWindow(
                    start=candidate,
                    end=end,
                    requested_energy_kwh=requested,
                    solar_energy_kwh=solar_energy,
                    grid_energy_kwh=grid_energy,
                    preserved_stationary_headroom_kwh=preserved,
                )
            candidate += step

    return best
