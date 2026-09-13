from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class ControllerSettings:
    """Physical capabilities and availability of the solar-surplus controller.

    Most controller-tuning fields remain for configuration compatibility and
    diagnostics. The planning model deliberately does not simulate the fast
    feedback loop: day-ahead forecasts cannot know second-by-second grid error,
    cloud transients, or controller timing. Only controller availability and the
    verified per-DPU maximum charging power constrain the physical forecast.
    """

    minimum_rate_w: float = 500.0
    maximum_rate_w: float = 3900.0
    rate_step_w: float = 100.0
    maximum_rate_increase_w: float = 800.0
    slow_import_decrease_w: float = 200.0
    moderate_import_decrease_w: float = 500.0
    preferred_import_w: float = 250.0
    import_hold_high_w: float = 350.0
    moderate_import_threshold_w: float = 1000.0
    severe_import_threshold_w: float = 2000.0
    start_export_w: float = 150.0
    export_gain: float = 1.0
    import_gain: float = 0.8
    minimum_solar_w: float = 150.0
    stop_all_w: float = 250.0
    start_2_w: float = 1800.0
    stop_2_w: float = 1100.0
    start_3_w: float = 3300.0
    stop_3_w: float = 2400.0
    enabled: bool = True
    source: str = "defaults"


@dataclass(frozen=True, slots=True)
class SimulationResult:
    ending_bank_socs_pct: tuple[float, float, float]
    aggregate_soc_pct: float
    stored_charge_kwh: float
    battery_ac_charge_kwh: float
    solar_to_battery_ac_kwh: float
    grid_to_battery_ac_kwh: float
    predicted_export_kwh: float
    predicted_grid_import_kwh: float
    capacity_limited_export_kwh: float
    power_limited_export_kwh: float
    control_limited_export_kwh: float
    peak_export_w: float
    export_minutes: float
    average_load_kw: float
    model: str


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def full_day_solar_curve(
    *,
    solar_forecast_kwh: float,
    daylight_hours: float,
    peak_elapsed_h: float,
) -> Callable[[float], float]:
    """Return an energy-conserving triangular full-day solar model in kW.

    We have a daily-energy forecast plus a peak-time estimate, not a trustworthy
    interval forecast. A simple curve that exactly conserves forecast kWh is
    preferable to inventing cloud detail we do not possess.
    """
    daylight_hours = max(float(daylight_hours), 0.0)
    solar_forecast_kwh = max(float(solar_forecast_kwh), 0.0)
    if daylight_hours <= 0 or solar_forecast_kwh <= 0:
        return lambda _elapsed_h: 0.0

    peak_elapsed_h = _clamp(float(peak_elapsed_h), 0.05, daylight_hours - 0.05)
    peak_kw = 2.0 * solar_forecast_kwh / daylight_hours

    def _curve(elapsed_h: float) -> float:
        elapsed_h = _clamp(float(elapsed_h), 0.0, daylight_hours)
        if elapsed_h <= peak_elapsed_h:
            return peak_kw * elapsed_h / peak_elapsed_h
        tail_h = daylight_hours - peak_elapsed_h
        return peak_kw * (daylight_hours - elapsed_h) / tail_h

    return _curve


def _allocate_solar_charge(
    *,
    requested_ac_kwh: float,
    dt_h: float,
    stored_kwh: list[float],
    capacities_kwh: tuple[float, float, float],
    charge_ceiling_kwh: tuple[float, float, float],
    charge_efficiency: float,
    maximum_rate_w: float,
    ignore_battery_capacity: bool,
) -> list[float]:
    """Allocate solar-only AC charging to the lowest-SOC eligible banks."""
    accepted = [0.0, 0.0, 0.0]
    remaining = max(float(requested_ac_kwh), 0.0)
    if remaining <= 0 or dt_h <= 0 or maximum_rate_w <= 0:
        return accepted

    order = sorted(
        range(3),
        key=lambda index: stored_kwh[index] / capacities_kwh[index],
    )
    per_bank_power_kwh = maximum_rate_w / 1000.0 * dt_h

    for index in order:
        if remaining <= 1e-12:
            break

        if ignore_battery_capacity:
            capacity_acceptance_kwh = per_bank_power_kwh
        elif charge_efficiency <= 0:
            capacity_acceptance_kwh = 0.0
        else:
            stored_headroom_kwh = max(
                charge_ceiling_kwh[index] - stored_kwh[index],
                0.0,
            )
            capacity_acceptance_kwh = stored_headroom_kwh / charge_efficiency

        bank_acceptance_kwh = min(
            remaining,
            per_bank_power_kwh,
            capacity_acceptance_kwh,
        )
        accepted[index] = max(bank_acceptance_kwh, 0.0)
        remaining -= accepted[index]

    return accepted


def simulate_energy_flow(
    *,
    duration_h: float,
    solar_power_kw: Callable[[float], float],
    average_load_kw: float,
    bank_socs_pct: tuple[float, float, float],
    bank_capacities_kwh: tuple[float, float, float],
    charge_limit_pct: float,
    charge_efficiency: float,
    controller: ControllerSettings,
    step_minutes: int = 1,
    ignore_battery_capacity: bool = False,
) -> SimulationResult:
    """Forecast protected-daytime AC flows from physical constraints.

    Solar serves house load first. The stationary battery never discharges in
    the solar window. Only true solar surplus may charge the battery. Grid import
    is therefore house-load deficit only, and charge-efficiency losses are never
    reclassified as export.

    Export exists only when surplus cannot be absorbed because the capture
    controller is unavailable, aggregate charge power is insufficient, or
    storage capacity is exhausted. Routine control-loop leakage is assumed zero
    until measured history justifies an empirical residual model.
    """
    duration_h = max(float(duration_h), 0.0)
    average_load_kw = max(float(average_load_kw), 0.0)
    charge_limit_pct = _clamp(float(charge_limit_pct), 0.0, 100.0)
    charge_efficiency = _clamp(float(charge_efficiency), 0.0, 1.0)
    step_minutes = max(int(step_minutes), 1)

    capacities = tuple(max(float(value), 0.001) for value in bank_capacities_kwh)
    if len(capacities) != 3:
        raise ValueError("exactly three bank capacities are required")
    if len(bank_socs_pct) != 3:
        raise ValueError("exactly three bank SOC values are required")

    stored = [
        capacity * _clamp(float(soc), 0.0, 100.0) / 100.0
        for capacity, soc in zip(capacities, bank_socs_pct)
    ]
    charge_ceiling = tuple(
        capacity * charge_limit_pct / 100.0 for capacity in capacities
    )

    stored_charge_kwh = 0.0
    battery_ac_charge_kwh = 0.0
    solar_to_battery_ac_kwh = 0.0
    grid_to_battery_ac_kwh = 0.0
    predicted_export_kwh = 0.0
    predicted_grid_import_kwh = 0.0
    capacity_limited_export_kwh = 0.0
    power_limited_export_kwh = 0.0
    control_limited_export_kwh = 0.0
    peak_export_w = 0.0
    export_minutes = 0.0

    step_h = step_minutes / 60.0
    elapsed_h = 0.0
    maximum_rate_w = max(float(controller.maximum_rate_w), 0.0)
    aggregate_charge_ceiling_w = maximum_rate_w * 3.0

    while elapsed_h < duration_h - 1e-9:
        dt_h = min(step_h, duration_h - elapsed_h)
        midpoint_h = elapsed_h + dt_h / 2.0
        solar_w = max(float(solar_power_kw(midpoint_h)), 0.0) * 1000.0
        load_w = average_load_kw * 1000.0

        house_deficit_w = max(load_w - solar_w, 0.0)
        natural_surplus_w = max(solar_w - load_w, 0.0)
        natural_surplus_kwh = natural_surplus_w / 1000.0 * dt_h
        predicted_grid_import_kwh += house_deficit_w / 1000.0 * dt_h

        if natural_surplus_kwh <= 0:
            elapsed_h += dt_h
            continue

        if not controller.enabled:
            control_limited_export_kwh += natural_surplus_kwh
            predicted_export_kwh += natural_surplus_kwh
            peak_export_w = max(peak_export_w, natural_surplus_w)
            export_minutes += dt_h * 60.0
            elapsed_h += dt_h
            continue

        # Power limitation is based on installed aggregate charge capability,
        # not how many banks currently have headroom. If a bank is full, the
        # resulting unabsorbed energy is a capacity constraint, not a power one.
        power_limited_w = max(
            natural_surplus_w - aggregate_charge_ceiling_w,
            0.0,
        )
        power_limited_kwh = power_limited_w / 1000.0 * dt_h
        power_limited_export_kwh += power_limited_kwh

        requested_charge_w = min(natural_surplus_w, aggregate_charge_ceiling_w)
        requested_charge_kwh = requested_charge_w / 1000.0 * dt_h
        accepted_by_bank = _allocate_solar_charge(
            requested_ac_kwh=requested_charge_kwh,
            dt_h=dt_h,
            stored_kwh=stored,
            capacities_kwh=capacities,
            charge_ceiling_kwh=charge_ceiling,
            charge_efficiency=charge_efficiency,
            maximum_rate_w=maximum_rate_w,
            ignore_battery_capacity=ignore_battery_capacity,
        )
        accepted_ac_kwh = sum(accepted_by_bank)
        capacity_limited_kwh = max(
            requested_charge_kwh - accepted_ac_kwh,
            0.0,
        )
        capacity_limited_export_kwh += capacity_limited_kwh

        export_kwh = power_limited_kwh + capacity_limited_kwh
        predicted_export_kwh += export_kwh
        export_w = export_kwh / dt_h * 1000.0 if dt_h > 0 else 0.0
        peak_export_w = max(peak_export_w, export_w)
        if export_kwh > 1e-9:
            export_minutes += dt_h * 60.0

        solar_to_battery_ac_kwh += accepted_ac_kwh
        battery_ac_charge_kwh += accepted_ac_kwh
        stored_gain_kwh = accepted_ac_kwh * charge_efficiency
        stored_charge_kwh += stored_gain_kwh

        if not ignore_battery_capacity:
            for index, accepted_ac in enumerate(accepted_by_bank):
                stored[index] = min(
                    stored[index] + accepted_ac * charge_efficiency,
                    charge_ceiling[index],
                )
        else:
            for index, accepted_ac in enumerate(accepted_by_bank):
                stored[index] += accepted_ac * charge_efficiency

        elapsed_h += dt_h

    ending_socs = tuple(
        100.0 * stored[index] / capacities[index]
        for index in range(3)
    )
    total_capacity = sum(capacities)
    aggregate_soc = 100.0 * sum(stored) / total_capacity

    return SimulationResult(
        ending_bank_socs_pct=ending_socs,
        aggregate_soc_pct=aggregate_soc,
        stored_charge_kwh=stored_charge_kwh,
        battery_ac_charge_kwh=battery_ac_charge_kwh,
        solar_to_battery_ac_kwh=solar_to_battery_ac_kwh,
        grid_to_battery_ac_kwh=grid_to_battery_ac_kwh,
        predicted_export_kwh=predicted_export_kwh,
        predicted_grid_import_kwh=predicted_grid_import_kwh,
        capacity_limited_export_kwh=capacity_limited_export_kwh,
        power_limited_export_kwh=power_limited_export_kwh,
        control_limited_export_kwh=control_limited_export_kwh,
        peak_export_w=peak_export_w,
        export_minutes=export_minutes,
        average_load_kw=average_load_kw,
        model=f"physical_surplus_v1:{controller.source}",
    )
