from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

try:
    from .simulation import ControllerSettings, full_day_solar_curve, simulate_energy_flow
except ImportError:  # pragma: no cover - direct unit-test import
    from simulation import ControllerSettings, full_day_solar_curve, simulate_energy_flow

STRATEGY_HOLD = "HOLD"
STRATEGY_USE_DISCRETIONARY_LOADS = "USE_DISCRETIONARY_LOADS"
STRATEGY_CREATE_HEADROOM = "CREATE_HEADROOM"
STRATEGY_PRESERVE_FOR_RESILIENCE = "PRESERVE_FOR_RESILIENCE"
STRATEGY_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class SolarPeriodPlan:
    strategy: str
    reason: str
    starting_soc_pct: float
    planned_start_soc_pct: float
    projected_max_soc_pct: float
    projected_sunset_soc_pct: float
    projected_charge_kwh: float
    predicted_export_kwh: float
    predicted_grid_import_kwh: float
    required_headroom_kwh: float
    available_headroom_kwh: float
    headroom_margin_kwh: float
    headroom_shortfall_kwh: float
    recommended_overnight_discharge_kwh: float
    discretionary_energy_kwh: float
    daylight_hours: float
    average_load_kw: float
    ending_bank_socs_pct: tuple[float, float, float]
    capacity_limited_export_kwh: float
    power_limited_export_kwh: float
    control_limited_export_kwh: float
    grid_to_battery_ac_kwh: float
    peak_export_w: float
    export_minutes: float
    model: str


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


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


def _aggregate_soc(
    socs: tuple[float, float, float],
    capacities: tuple[float, float, float],
) -> float:
    return 100.0 * sum(
        capacity * soc / 100.0 for capacity, soc in zip(capacities, socs)
    ) / sum(capacities)


def _discharge_banks(
    socs: tuple[float, float, float],
    capacities: tuple[float, float, float],
    discharge_kwh: float,
    reserve_pct: float,
) -> tuple[float, float, float]:
    stored = [capacity * soc / 100.0 for capacity, soc in zip(capacities, socs)]
    floors = [capacity * reserve_pct / 100.0 for capacity in capacities]
    available = [max(value - floor, 0.0) for value, floor in zip(stored, floors)]
    total_available = sum(available)
    if total_available <= 0 or discharge_kwh <= 0:
        return socs
    fraction = min(discharge_kwh / total_available, 1.0)
    ending = [value - room * fraction for value, room in zip(stored, available)]
    return tuple(
        100.0 * value / capacity for value, capacity in zip(ending, capacities)
    )


def plan_solar_period(
    *,
    sunrise: datetime,
    sunset: datetime,
    current_soc_pct: float,
    capacity_kwh: float,
    charge_limit_pct: float,
    minimum_reserve_pct: float,
    solar_forecast_kwh: float,
    base_load_power_w: float,
    peak_time: datetime | None,
    harvest_capture_factor: float = 1.0,
    charge_efficiency: float = 0.90,
    storm_active: bool = False,
    ev_soc_pct: float | None = None,
    ev_target_soc_pct: float = 100.0,
    ev_home: bool | None = None,
    ev_discretionary_allowed: bool = False,
    discretionary_threshold_kwh: float = 1.0,
    allow_presolar_discharge: bool = True,
    bank_socs_pct: tuple[float, float, float] | None = None,
    bank_capacities_kwh: tuple[float, float, float] | None = None,
    controller_settings: ControllerSettings | None = None,
    step_minutes: int = 1,
) -> SolarPeriodPlan:
    """Plan one complete solar period using physical AC-flow constraints.

    ``harvest_capture_factor`` remains in the signature for config-entry
    compatibility but is intentionally not used. Forecast derating is not a
    physical export path: energy is exported only when the modeled controller,
    battery capacity, or charging-power limits cannot absorb it.
    """
    del harvest_capture_factor

    capacity_kwh = max(float(capacity_kwh), 0.001)
    current_soc_pct = _clamp(float(current_soc_pct), 0.0, 100.0)
    charge_limit_pct = _clamp(float(charge_limit_pct), 0.0, 100.0)
    minimum_reserve_pct = _clamp(float(minimum_reserve_pct), 0.0, 100.0)
    solar_forecast_kwh = max(float(solar_forecast_kwh), 0.0)
    average_load_kw = max(float(base_load_power_w), 0.0) / 1000.0
    charge_efficiency = _clamp(float(charge_efficiency), 0.0, 1.0)
    discretionary_threshold_kwh = max(float(discretionary_threshold_kwh), 0.0)
    controller_settings = controller_settings or ControllerSettings()

    daylight_hours = max((sunset - sunrise).total_seconds() / 3600.0, 0.0)
    if daylight_hours <= 0:
        raise ValueError("sunset must be after sunrise")

    capacities = _normalize_capacities(capacity_kwh, bank_capacities_kwh)
    starting_socs = _normalize_socs(current_soc_pct, bank_socs_pct)
    starting_soc_pct = _aggregate_soc(starting_socs, capacities)

    peak_elapsed_h = daylight_hours / 2.0
    if peak_time is not None:
        candidate = (peak_time - sunrise).total_seconds() / 3600.0
        if 0.05 < candidate < daylight_hours - 0.05:
            peak_elapsed_h = candidate
    solar_curve = full_day_solar_curve(
        solar_forecast_kwh=solar_forecast_kwh,
        daylight_hours=daylight_hours,
        peak_elapsed_h=peak_elapsed_h,
    )

    baseline = simulate_energy_flow(
        duration_h=daylight_hours,
        solar_power_kw=solar_curve,
        average_load_kw=average_load_kw,
        bank_socs_pct=starting_socs,
        bank_capacities_kwh=capacities,
        charge_limit_pct=charge_limit_pct,
        charge_efficiency=charge_efficiency,
        controller=controller_settings,
        step_minutes=step_minutes,
    )
    unlimited = simulate_energy_flow(
        duration_h=daylight_hours,
        solar_power_kw=solar_curve,
        average_load_kw=average_load_kw,
        bank_socs_pct=starting_socs,
        bank_capacities_kwh=capacities,
        charge_limit_pct=charge_limit_pct,
        charge_efficiency=charge_efficiency,
        controller=controller_settings,
        step_minutes=step_minutes,
        ignore_battery_capacity=True,
    )

    current_stored_kwh = sum(
        capacity * soc / 100.0 for capacity, soc in zip(capacities, starting_socs)
    )
    charge_ceiling_kwh = sum(capacities) * charge_limit_pct / 100.0
    available_headroom_kwh = max(charge_ceiling_kwh - current_stored_kwh, 0.0)
    required_headroom_kwh = unlimited.stored_charge_kwh
    headroom_margin_kwh = available_headroom_kwh - required_headroom_kwh
    headroom_shortfall_kwh = max(-headroom_margin_kwh, 0.0)

    reserve_floor_kwh = sum(capacities) * minimum_reserve_pct / 100.0
    stored_above_reserve_kwh = max(current_stored_kwh - reserve_floor_kwh, 0.0)
    possible_discharge_kwh = min(headroom_shortfall_kwh, stored_above_reserve_kwh)

    ev_needs_charge = (
        ev_soc_pct is not None
        and float(ev_soc_pct) < float(ev_target_soc_pct) - 1.0
    )
    ev_available = ev_home is not False and ev_needs_charge
    capacity_export_material = (
        baseline.capacity_limited_export_kwh >= discretionary_threshold_kwh
    )

    strategy = STRATEGY_HOLD
    recommended_discharge_kwh = 0.0

    if storm_active:
        strategy = STRATEGY_PRESERVE_FOR_RESILIENCE
        reason = (
            "Storm protection is active; preserve stored energy and do not "
            "create headroom."
        )
    elif capacity_export_material and ev_discretionary_allowed and ev_available:
        strategy = STRATEGY_USE_DISCRETIONARY_LOADS
        reason = (
            f"About {baseline.predicted_export_kwh:.2f} kWh is physically "
            "at risk of export; a controllable EV/flexible load can be used "
            "before cycling the stationary battery."
        )
    elif (
        headroom_shortfall_kwh > 0.25
        and allow_presolar_discharge
        and possible_discharge_kwh > 0.05
    ):
        strategy = STRATEGY_CREATE_HEADROOM
        recommended_discharge_kwh = possible_discharge_kwh
        reason = (
            f"Battery headroom is short by {headroom_shortfall_kwh:.2f} kWh; "
            f"create no more than {recommended_discharge_kwh:.2f} kWh before "
            "the solar window while respecting the effective reserve floor."
        )
    elif headroom_shortfall_kwh > 0.25 and not allow_presolar_discharge:
        if baseline.predicted_export_kwh >= discretionary_threshold_kwh:
            strategy = STRATEGY_USE_DISCRETIONARY_LOADS
            reason = (
                f"About {baseline.predicted_export_kwh:.2f} kWh remains at "
                "physical export risk; daylight discharge is prohibited, so "
                "only a controllable flexible load should be considered."
            )
        else:
            reason = (
                "Additional headroom could help, but daylight battery discharge "
                "is prohibited; preserve stored solar."
            )
    elif headroom_shortfall_kwh > 0.25:
        reason = (
            "Additional headroom could help, but the effective reserve floor "
            "prevents a recommended discharge."
        )
    elif baseline.predicted_export_kwh >= discretionary_threshold_kwh:
        strategy = STRATEGY_USE_DISCRETIONARY_LOADS
        reason = (
            f"About {baseline.predicted_export_kwh:.2f} kWh remains at physical "
            "export risk from controller/power constraints; use only a "
            "controllable flexible load."
        )
    else:
        reason = (
            f"Existing headroom exceeds modeled storage need by "
            f"{max(headroom_margin_kwh, 0.0):.2f} kWh and physical export risk "
            f"is only {baseline.predicted_export_kwh:.2f} kWh; preserve stored solar."
        )

    planned_socs = _discharge_banks(
        starting_socs,
        capacities,
        recommended_discharge_kwh,
        minimum_reserve_pct,
    )
    planned_start_soc_pct = _aggregate_soc(planned_socs, capacities)
    final_simulation = (
        simulate_energy_flow(
            duration_h=daylight_hours,
            solar_power_kw=solar_curve,
            average_load_kw=average_load_kw,
            bank_socs_pct=planned_socs,
            bank_capacities_kwh=capacities,
            charge_limit_pct=charge_limit_pct,
            charge_efficiency=charge_efficiency,
            controller=controller_settings,
            step_minutes=step_minutes,
        )
        if recommended_discharge_kwh > 0
        else baseline
    )

    discretionary_energy_kwh = (
        final_simulation.predicted_export_kwh
        if final_simulation.predicted_export_kwh >= discretionary_threshold_kwh
        else 0.0
    )

    return SolarPeriodPlan(
        strategy=strategy,
        reason=reason,
        starting_soc_pct=starting_soc_pct,
        planned_start_soc_pct=planned_start_soc_pct,
        projected_max_soc_pct=final_simulation.aggregate_soc_pct,
        projected_sunset_soc_pct=final_simulation.aggregate_soc_pct,
        projected_charge_kwh=final_simulation.stored_charge_kwh,
        predicted_export_kwh=final_simulation.predicted_export_kwh,
        predicted_grid_import_kwh=final_simulation.predicted_grid_import_kwh,
        required_headroom_kwh=required_headroom_kwh,
        available_headroom_kwh=available_headroom_kwh,
        headroom_margin_kwh=headroom_margin_kwh,
        headroom_shortfall_kwh=headroom_shortfall_kwh,
        recommended_overnight_discharge_kwh=recommended_discharge_kwh,
        discretionary_energy_kwh=discretionary_energy_kwh,
        daylight_hours=daylight_hours,
        average_load_kw=average_load_kw,
        ending_bank_socs_pct=final_simulation.ending_bank_socs_pct,
        capacity_limited_export_kwh=final_simulation.capacity_limited_export_kwh,
        power_limited_export_kwh=final_simulation.power_limited_export_kwh,
        control_limited_export_kwh=final_simulation.control_limited_export_kwh,
        grid_to_battery_ac_kwh=final_simulation.grid_to_battery_ac_kwh,
        peak_export_w=final_simulation.peak_export_w,
        export_minutes=final_simulation.export_minutes,
        model=final_simulation.model,
    )
