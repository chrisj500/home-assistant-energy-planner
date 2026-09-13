from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

try:
    from .simulation import ControllerSettings, simulate_energy_flow
except ImportError:  # pragma: no cover - direct unit-test import
    from simulation import ControllerSettings, simulate_energy_flow


@dataclass(frozen=True, slots=True)
class SunsetProjection:
    projected_soc: float
    projected_stored_kwh: float
    projected_charge_kwh: float
    projected_captured_ac_kwh: float
    projected_available_ac_kwh: float
    predicted_export_kwh: float
    predicted_grid_import_kwh: float
    capacity_limited_export_kwh: float
    power_limited_export_kwh: float
    control_limited_export_kwh: float
    grid_to_battery_ac_kwh: float
    peak_export_w: float
    export_minutes: float
    ending_bank_socs_pct: tuple[float, float, float]
    hours_to_sunset: float
    average_load_kw: float
    modeled_solar_start_kw: float
    model: str


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _solar_power_kw(
    elapsed_h: float,
    *,
    duration_h: float,
    current_solar_kw: float,
    remaining_solar_kwh: float,
    hours_to_peak: float | None,
) -> tuple[float, float, str]:
    """Return an energy-conserving remaining-day solar curve."""
    if duration_h <= 0 or remaining_solar_kwh <= 0:
        return 0.0, 0.0, "none"

    elapsed_h = _clamp(elapsed_h, 0.0, duration_h)
    current_solar_kw = max(current_solar_kw, 0.0)

    if hours_to_peak is not None and 0.05 < hours_to_peak < duration_h:
        t_peak = hours_to_peak
        solved_peak = (
            2.0 * remaining_solar_kwh - current_solar_kw * t_peak
        ) / duration_h
        peak_kw = max(solved_peak, current_solar_kw, 0.0)

        modeled_energy = (
            0.5 * current_solar_kw * t_peak
            + 0.5 * peak_kw * duration_h
        )
        if modeled_energy > remaining_solar_kwh * 1.15:
            start_kw = 2.0 * remaining_solar_kwh / duration_h
            power = max(start_kw * (1.0 - elapsed_h / duration_h), 0.0)
            return power, start_kw, "post_peak_triangle_fallback"

        if elapsed_h <= t_peak:
            fraction = elapsed_h / t_peak
            power = current_solar_kw + (peak_kw - current_solar_kw) * fraction
        else:
            tail_h = duration_h - t_peak
            fraction = (
                (elapsed_h - t_peak) / tail_h if tail_h > 0 else 1.0
            )
            power = peak_kw * (1.0 - fraction)
        return max(power, 0.0), current_solar_kw, "pre_peak_piecewise_linear"

    start_kw = 2.0 * remaining_solar_kwh / duration_h
    power = max(start_kw * (1.0 - elapsed_h / duration_h), 0.0)
    return power, start_kw, "post_peak_linear"


def _normalize_capacities(
    capacity_kwh: float,
    bank_capacities_kwh: tuple[float, float, float] | None,
) -> tuple[float, float, float]:
    if bank_capacities_kwh is not None:
        values = tuple(max(float(value), 0.001) for value in bank_capacities_kwh)
        total = sum(values)
        if total > 0:
            scale = capacity_kwh / total
            return tuple(value * scale for value in values)
    return (capacity_kwh / 3.0,) * 3


def _normalize_socs(
    current_soc_pct: float,
    bank_socs_pct: tuple[float, float, float] | None,
) -> tuple[float, float, float]:
    if bank_socs_pct is None:
        return (current_soc_pct,) * 3
    return tuple(_clamp(float(value), 0.0, 100.0) for value in bank_socs_pct)


def project_sunset_soc(
    *,
    now: datetime,
    sunset: datetime,
    current_soc_pct: float,
    capacity_kwh: float,
    charge_limit_pct: float,
    remaining_solar_kwh: float,
    expected_load_remaining_kwh: float,
    current_solar_w: float,
    peak_time: datetime | None,
    preferred_import_w: float = 250.0,
    harvest_capture_factor: float = 1.0,
    charge_efficiency: float = 0.90,
    bank_socs_pct: tuple[float, float, float] | None = None,
    bank_capacities_kwh: tuple[float, float, float] | None = None,
    controller_settings: ControllerSettings | None = None,
    step_minutes: int = 1,
) -> SunsetProjection:
    """Project sunset SOC and physical grid flows without daytime discharge.

    ``harvest_capture_factor`` is accepted only for backward compatibility.
    It is not an export assumption and does not remove forecast solar energy.
    """
    del harvest_capture_factor

    capacity_kwh = max(float(capacity_kwh), 0.001)
    current_soc_pct = _clamp(float(current_soc_pct), 0.0, 100.0)
    charge_limit_pct = _clamp(float(charge_limit_pct), 0.0, 100.0)
    remaining_solar_kwh = max(float(remaining_solar_kwh), 0.0)
    expected_load_remaining_kwh = max(float(expected_load_remaining_kwh), 0.0)
    charge_efficiency = _clamp(float(charge_efficiency), 0.0, 1.0)
    duration_h = max((sunset - now).total_seconds() / 3600.0, 0.0)

    settings = controller_settings or ControllerSettings(
        preferred_import_w=max(float(preferred_import_w), 0.0)
    )
    capacities = _normalize_capacities(capacity_kwh, bank_capacities_kwh)
    starting_socs = _normalize_socs(current_soc_pct, bank_socs_pct)
    starting_stored_kwh = sum(
        capacity * soc / 100.0
        for capacity, soc in zip(capacities, starting_socs)
    )

    if duration_h <= 0:
        return SunsetProjection(
            projected_soc=current_soc_pct,
            projected_stored_kwh=starting_stored_kwh,
            projected_charge_kwh=0.0,
            projected_captured_ac_kwh=0.0,
            projected_available_ac_kwh=0.0,
            predicted_export_kwh=0.0,
            predicted_grid_import_kwh=0.0,
            capacity_limited_export_kwh=0.0,
            power_limited_export_kwh=0.0,
            control_limited_export_kwh=0.0,
            grid_to_battery_ac_kwh=0.0,
            peak_export_w=0.0,
            export_minutes=0.0,
            ending_bank_socs_pct=starting_socs,
            hours_to_sunset=0.0,
            average_load_kw=0.0,
            modeled_solar_start_kw=0.0,
            model="none",
        )

    average_load_kw = expected_load_remaining_kwh / duration_h
    hours_to_peak = None
    if peak_time is not None:
        hours_to_peak = (peak_time - now).total_seconds() / 3600.0

    current_solar_kw = max(float(current_solar_w), 0.0) / 1000.0
    _initial_power, modeled_start_kw, solar_model = _solar_power_kw(
        0.0,
        duration_h=duration_h,
        current_solar_kw=current_solar_kw,
        remaining_solar_kwh=remaining_solar_kwh,
        hours_to_peak=hours_to_peak,
    )

    def solar_curve(elapsed_h: float) -> float:
        power, _start, _model = _solar_power_kw(
            elapsed_h,
            duration_h=duration_h,
            current_solar_kw=current_solar_kw,
            remaining_solar_kwh=remaining_solar_kwh,
            hours_to_peak=hours_to_peak,
        )
        return power

    simulation = simulate_energy_flow(
        duration_h=duration_h,
        solar_power_kw=solar_curve,
        average_load_kw=average_load_kw,
        bank_socs_pct=starting_socs,
        bank_capacities_kwh=capacities,
        charge_limit_pct=charge_limit_pct,
        charge_efficiency=charge_efficiency,
        controller=settings,
        step_minutes=step_minutes,
    )
    projected_stored = sum(
        capacity * soc / 100.0
        for capacity, soc in zip(capacities, simulation.ending_bank_socs_pct)
    )

    # Available AC opportunity means modeled solar above the house load before
    # controller actions. It is descriptive only; export comes from simulation.
    available_ac_kwh = 0.0
    step_h = max(int(step_minutes), 1) / 60.0
    elapsed_h = 0.0
    while elapsed_h < duration_h - 1e-9:
        dt_h = min(step_h, duration_h - elapsed_h)
        midpoint_h = elapsed_h + dt_h / 2.0
        available_ac_kwh += max(
            solar_curve(midpoint_h) - average_load_kw,
            0.0,
        ) * dt_h
        elapsed_h += dt_h

    return SunsetProjection(
        projected_soc=simulation.aggregate_soc_pct,
        projected_stored_kwh=projected_stored,
        projected_charge_kwh=simulation.stored_charge_kwh,
        projected_captured_ac_kwh=simulation.solar_to_battery_ac_kwh,
        projected_available_ac_kwh=available_ac_kwh,
        predicted_export_kwh=simulation.predicted_export_kwh,
        predicted_grid_import_kwh=simulation.predicted_grid_import_kwh,
        capacity_limited_export_kwh=simulation.capacity_limited_export_kwh,
        power_limited_export_kwh=simulation.power_limited_export_kwh,
        control_limited_export_kwh=simulation.control_limited_export_kwh,
        grid_to_battery_ac_kwh=simulation.grid_to_battery_ac_kwh,
        peak_export_w=simulation.peak_export_w,
        export_minutes=simulation.export_minutes,
        ending_bank_socs_pct=simulation.ending_bank_socs_pct,
        hours_to_sunset=duration_h,
        average_load_kw=average_load_kw,
        modeled_solar_start_kw=modeled_start_kw,
        model=f"{solar_model}+{simulation.model}",
    )
