from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

STRATEGY_HOLD = "HOLD"
STRATEGY_USE_DISCRETIONARY_LOADS = "USE_DISCRETIONARY_LOADS"
STRATEGY_CREATE_HEADROOM = "CREATE_HEADROOM"
STRATEGY_PRESERVE_FOR_RESILIENCE = "PRESERVE_FOR_RESILIENCE"
STRATEGY_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class SolarPeriodPlan:
    strategy: str
    reason: str
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
    model: str


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


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
    harvest_capture_factor: float = 0.88,
    charge_efficiency: float = 0.90,
    storm_active: bool = False,
    ev_soc_pct: float | None = None,
    ev_target_soc_pct: float = 100.0,
    ev_home: bool | None = None,
    discretionary_threshold_kwh: float = 1.0,
    step_minutes: int = 5,
) -> SolarPeriodPlan:
    """Plan one complete solar period without routine battery discharge.

    Solar serves house load first. Only modeled AC surplus is available to
    charge the stationary battery. A battery discharge is recommended only
    when storage headroom is expected to be insufficient, the reserve floor
    allows it, and a concrete EV opportunity is not preferred first.
    """
    capacity_kwh = max(float(capacity_kwh), 0.001)
    current_soc_pct = _clamp(float(current_soc_pct), 0.0, 100.0)
    charge_limit_pct = _clamp(float(charge_limit_pct), 0.0, 100.0)
    minimum_reserve_pct = _clamp(float(minimum_reserve_pct), 0.0, 100.0)
    solar_forecast_kwh = max(float(solar_forecast_kwh), 0.0)
    average_load_kw = max(float(base_load_power_w), 0.0) / 1000.0
    harvest_capture_factor = _clamp(float(harvest_capture_factor), 0.0, 1.0)
    charge_efficiency = _clamp(float(charge_efficiency), 0.0, 1.0)
    discretionary_threshold_kwh = max(float(discretionary_threshold_kwh), 0.0)
    step_minutes = max(int(step_minutes), 1)

    daylight_hours = max((sunset - sunrise).total_seconds() / 3600.0, 0.0)
    if daylight_hours <= 0:
        raise ValueError("sunset must be after sunrise")

    stored_kwh = capacity_kwh * current_soc_pct / 100.0
    charge_ceiling_kwh = capacity_kwh * charge_limit_pct / 100.0
    available_headroom_kwh = max(charge_ceiling_kwh - stored_kwh, 0.0)

    peak_elapsed_h = daylight_hours / 2.0
    if peak_time is not None:
        candidate = (peak_time - sunrise).total_seconds() / 3600.0
        if 0.05 < candidate < daylight_hours - 0.05:
            peak_elapsed_h = candidate

    # A triangular curve conserves the daily forecast exactly regardless of
    # where the peak occurs because its area is 1/2 * peak * daylight_hours.
    peak_kw = (
        2.0 * solar_forecast_kwh / daylight_hours
        if solar_forecast_kwh > 0
        else 0.0
    )

    projected_stored_kwh = min(stored_kwh, charge_ceiling_kwh)
    projected_charge_kwh = 0.0
    predicted_export_kwh = 0.0
    predicted_grid_import_kwh = 0.0
    required_headroom_kwh = 0.0

    step_h = step_minutes / 60.0
    elapsed_h = 0.0
    while elapsed_h < daylight_hours - 1e-9:
        dt_h = min(step_h, daylight_hours - elapsed_h)
        midpoint_h = elapsed_h + dt_h / 2.0

        if midpoint_h <= peak_elapsed_h:
            solar_kw = (
                peak_kw * midpoint_h / peak_elapsed_h
                if peak_elapsed_h > 0
                else peak_kw
            )
        else:
            tail_h = daylight_hours - peak_elapsed_h
            solar_kw = (
                peak_kw * (daylight_hours - midpoint_h) / tail_h
                if tail_h > 0
                else 0.0
            )

        net_kw = solar_kw - average_load_kw
        if net_kw > 0:
            surplus_ac_kwh = net_kw * dt_h
            battery_eligible_ac_kwh = surplus_ac_kwh * harvest_capture_factor
            potential_stored_kwh = battery_eligible_ac_kwh * charge_efficiency
            required_headroom_kwh += potential_stored_kwh

            headroom_left_kwh = max(
                charge_ceiling_kwh - projected_stored_kwh, 0.0
            )
            accepted_stored_kwh = min(
                potential_stored_kwh, headroom_left_kwh
            )
            projected_stored_kwh += accepted_stored_kwh
            projected_charge_kwh += accepted_stored_kwh

            accepted_ac_kwh = (
                accepted_stored_kwh / charge_efficiency
                if charge_efficiency > 0
                else 0.0
            )
            predicted_export_kwh += max(
                surplus_ac_kwh - accepted_ac_kwh, 0.0
            )
        else:
            predicted_grid_import_kwh += -net_kw * dt_h

        elapsed_h += dt_h

    headroom_margin_kwh = available_headroom_kwh - required_headroom_kwh
    headroom_shortfall_kwh = max(-headroom_margin_kwh, 0.0)
    reserve_floor_kwh = capacity_kwh * minimum_reserve_pct / 100.0
    stored_above_reserve_kwh = max(stored_kwh - reserve_floor_kwh, 0.0)
    recommended_discharge_kwh = min(
        headroom_shortfall_kwh, stored_above_reserve_kwh
    )

    ev_needs_charge = (
        ev_soc_pct is not None
        and float(ev_soc_pct) < float(ev_target_soc_pct) - 1.0
    )
    ev_available = ev_home is not False and ev_needs_charge
    discretionary_energy_kwh = (
        predicted_export_kwh
        if predicted_export_kwh >= discretionary_threshold_kwh
        else 0.0
    )

    if storm_active:
        strategy = STRATEGY_PRESERVE_FOR_RESILIENCE
        recommended_discharge_kwh = 0.0
        reason = (
            "Storm protection is active; preserve stored energy and do not "
            "create headroom."
        )
    elif (
        headroom_shortfall_kwh > 0.25
        and ev_available
        and predicted_export_kwh >= discretionary_threshold_kwh
    ):
        strategy = STRATEGY_USE_DISCRETIONARY_LOADS
        recommended_discharge_kwh = 0.0
        reason = (
            f"About {predicted_export_kwh:.2f} kWh may otherwise export; "
            "use the EV or another flexible load before cycling the stationary "
            "battery."
        )
    elif headroom_shortfall_kwh > 0.25 and recommended_discharge_kwh > 0.05:
        strategy = STRATEGY_CREATE_HEADROOM
        reason = (
            f"Battery headroom is short by {headroom_shortfall_kwh:.2f} kWh; "
            f"create no more than {recommended_discharge_kwh:.2f} kWh while "
            "respecting the reserve floor."
        )
    elif headroom_shortfall_kwh > 0.25:
        strategy = STRATEGY_HOLD
        recommended_discharge_kwh = 0.0
        reason = (
            "Additional headroom may be useful, but the reserve floor prevents "
            "a recommended battery discharge."
        )
    elif (
        predicted_export_kwh >= discretionary_threshold_kwh
        and ev_available
    ):
        strategy = STRATEGY_USE_DISCRETIONARY_LOADS
        recommended_discharge_kwh = 0.0
        reason = (
            f"About {predicted_export_kwh:.2f} kWh may otherwise export; "
            "schedule the EV or another flexible load in the solar window."
        )
    else:
        strategy = STRATEGY_HOLD
        recommended_discharge_kwh = 0.0
        reason = (
            f"Existing headroom exceeds modeled storage need by "
            f"{max(headroom_margin_kwh, 0.0):.2f} kWh; preserve stored solar."
        )

    projected_soc_pct = 100.0 * projected_stored_kwh / capacity_kwh

    return SolarPeriodPlan(
        strategy=strategy,
        reason=reason,
        projected_max_soc_pct=projected_soc_pct,
        projected_sunset_soc_pct=projected_soc_pct,
        projected_charge_kwh=projected_charge_kwh,
        predicted_export_kwh=predicted_export_kwh,
        predicted_grid_import_kwh=predicted_grid_import_kwh,
        required_headroom_kwh=required_headroom_kwh,
        available_headroom_kwh=available_headroom_kwh,
        headroom_margin_kwh=headroom_margin_kwh,
        headroom_shortfall_kwh=headroom_shortfall_kwh,
        recommended_overnight_discharge_kwh=recommended_discharge_kwh,
        discretionary_energy_kwh=discretionary_energy_kwh,
        daylight_hours=daylight_hours,
        average_load_kw=average_load_kw,
        model="full_day_triangle",
    )
