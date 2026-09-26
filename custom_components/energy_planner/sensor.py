from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EnergyPlannerCoordinator


@dataclass(frozen=True, kw_only=True)
class EnergyPlannerSensorDescription(SensorEntityDescription):
    data_key: str


def _energy(key: str, data_key: str, name: str, *, storage: bool = False):
    return EnergyPlannerSensorDescription(
        key=key,
        data_key=data_key,
        name=name,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=(
            SensorDeviceClass.ENERGY_STORAGE if storage else SensorDeviceClass.ENERGY
        ),
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    )


def _total_energy(key: str, data_key: str, name: str):
    return EnergyPlannerSensorDescription(
        key=key,
        data_key=data_key,
        name=name,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
    )


def _soc(key: str, data_key: str, name: str):
    return EnergyPlannerSensorDescription(
        key=key,
        data_key=data_key,
        name=name,
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    )


def _percent(key: str, data_key: str, name: str):
    return EnergyPlannerSensorDescription(
        key=key,
        data_key=data_key,
        name=name,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    )


SENSORS = (
    EnergyPlannerSensorDescription(key="solar_learning_status", data_key="solar_learning_status", name="Solar Learning Status"),
    EnergyPlannerSensorDescription(key="solar_learning_usable_days", data_key="solar_learning_usable_days", name="Solar Learning Usable Days"),
    EnergyPlannerSensorDescription(key="solar_learning_scored_forecasts", data_key="solar_learning_scored_forecasts", name="Solar Learning Scored Forecasts"),
    EnergyPlannerSensorDescription(key="hvac_daily_electricity", data_key="hvac_daily_electricity_kwh", name="HVAC Daily Electricity", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING),
    EnergyPlannerSensorDescription(key="hvac_status", data_key="hvac_status", name="HVAC Model Status"),
    EnergyPlannerSensorDescription(key="hvac_electrical_power", data_key="hvac_electrical_power_w", name="HVAC Electrical Power", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    EnergyPlannerSensorDescription(key="forecast_confidence", data_key="forecast_confidence", name="Forecast Confidence"),
    EnergyPlannerSensorDescription(key="forecast_reliability_status", data_key="forecast_reliability_status", name="Forecast Reliability Status"),
    EnergyPlannerSensorDescription(key="forecast_reliability_reason", data_key="forecast_reliability_reason", name="Forecast Reliability Reason"),
    EnergyPlannerSensorDescription(key="forecast_error_samples", data_key="forecast_error_samples", name="Forecast Error Samples"),
    EnergyPlannerSensorDescription(key="forecast_learning_progress", data_key="forecast_learning_progress", name="Forecast Learning Progress"),
    EnergyPlannerSensorDescription(key="storm_safety_status", data_key="storm_safety_status", name="Storm Safety Status"),
    _soc("weighted_soc", "weighted_soc", "Whole Bank SOC"),
    _energy(
        "battery_capacity",
        "battery_capacity_kwh",
        "Effective Battery Capacity",
        storage=True,
    ),
    EnergyPlannerSensorDescription(
        key="battery_pack_count",
        data_key="battery_pack_count_total",
        name="Battery Pack Count",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="battery_topology_source",
        data_key="battery_topology_source",
        name="Battery Topology Source",
    ),
    _energy("stored_energy", "stored_energy", "Whole Bank Stored Energy", storage=True),
    _energy("battery_headroom", "battery_headroom", "Battery Headroom", storage=True),
    EnergyPlannerSensorDescription(
        key="battery_bank_power",
        data_key="battery_bank_power_w",
        name="Battery Bank Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    _total_energy(
        "battery_energy_charged",
        "battery_energy_charged_kwh",
        "Battery Energy Charged",
    ),
    _total_energy(
        "battery_energy_discharged",
        "battery_energy_discharged_kwh",
        "Battery Energy Discharged",
    ),
    _energy("upcoming_solar", "upcoming_solar", "Upcoming Solar"),
    _soc("projected_sunset_soc", "projected_sunset_soc", "Live Projected Sunset SOC"),
    _energy(
        "projected_charge_to_sunset",
        "projected_charge_to_sunset",
        "Live Projected Battery Charge to Sunset",
        storage=True,
    ),
    _energy(
        "projection_available_ac",
        "projection_available_ac",
        "Live Projected Charge Opportunity to Sunset",
    ),
    EnergyPlannerSensorDescription(
        key="projection_model",
        data_key="projection_model",
        name="Live Sunset Projection Model",
    ),

    # Optional Forecast.Solar shadow observability. These sensors never drive
    # strategy or headroom release.
    EnergyPlannerSensorDescription(
        key="forecast_solar_enhancement_status",
        data_key="forecast_solar_enhancement_status",
        name="Forecast.Solar Enhancement Status",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_fallback_reason",
        data_key="forecast_solar_fallback_reason",
        name="Forecast.Solar Fallback Reason",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_account_type",
        data_key="forecast_solar_account_type",
        name="Forecast.Solar Account Type",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_interval_resolution_min",
        data_key="forecast_solar_interval_resolution_min",
        name="Forecast.Solar Interval Resolution",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_horizon_days",
        data_key="forecast_solar_horizon_days",
        name="Forecast.Solar Horizon",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_interval_points",
        data_key="forecast_solar_interval_points",
        name="Forecast.Solar Interval Points",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _energy(
        "forecast_solar_paid_remaining_today_raw",
        "forecast_solar_paid_remaining_today_raw",
        "Forecast.Solar Paid Raw Remaining Today",
    ),
    _soc(
        "forecast_solar_shadow_projected_sunset_soc",
        "forecast_solar_shadow_projected_sunset_soc",
        "Forecast.Solar Shadow Projected Sunset SOC",
    ),
    _percent(
        "forecast_solar_shadow_sunset_soc_delta",
        "forecast_solar_shadow_sunset_soc_delta",
        "Forecast.Solar Shadow Sunset SOC Delta",
    ),
    _energy(
        "forecast_solar_shadow_charge_to_sunset",
        "forecast_solar_shadow_charge_to_sunset",
        "Forecast.Solar Shadow Battery Charge to Sunset",
        storage=True,
    ),
    _energy(
        "forecast_solar_shadow_grid_import_today",
        "forecast_solar_shadow_grid_import_today",
        "Forecast.Solar Shadow Daylight Grid Import",
    ),
    _energy(
        "forecast_solar_shadow_export_today",
        "forecast_solar_shadow_export_today",
        "Forecast.Solar Shadow Export Today",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_shadow_scale_factor",
        data_key="forecast_solar_shadow_scale_factor",
        name="Forecast.Solar Shadow Energy Scale Factor",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_shadow_model",
        data_key="forecast_solar_shadow_model",
        name="Forecast.Solar Shadow Model",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_professional_status",
        data_key="forecast_solar_professional_status",
        name="Forecast.Solar Professional Data Status",
    ),
    _percent(
        "forecast_solar_weather_sky_now_pct",
        "forecast_solar_weather_sky_now_pct",
        "Forecast.Solar Weather Sky Now",
    ),
    _percent(
        "forecast_solar_weather_sky_next_6h_pct",
        "forecast_solar_weather_sky_next_6h_pct",
        "Forecast.Solar Weather Sky Next 6 Hours",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_solar_weather_temperature_now",
        data_key="forecast_solar_weather_temperature_now",
        name="Forecast.Solar Weather Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),

    # Export-first forecast risk. This is independent of whether an
    # action is currently authorized by the reliability gate.
    EnergyPlannerSensorDescription(
        key="forecast_export_risk_date",
        data_key="forecast_export_risk_date",
        name="Forecast Export Risk Date",
    ),
    _energy(
        "forecast_export_headroom",
        "forecast_export_headroom_kwh",
        "Forecast Export-Defense Headroom",
        storage=True,
    ),
    _energy(
        "forecast_export_wall_energy",
        "forecast_export_wall_energy_kwh",
        "Forecast Flexible-Load Energy Needed",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_export_risk_reason",
        data_key="forecast_export_risk_reason",
        name="Forecast Export Risk Reason",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_export_model",
        data_key="forecast_export_model",
        name="Forecast Export Risk Model",
    ),
    EnergyPlannerSensorDescription(
        key="forecast_objective",
        data_key="forecast_objective",
        name="Forecast Objective",
    ),

    # No-action counterfactual and measured solar-capture ledger.
    EnergyPlannerSensorDescription(
        key="counterfactual_status",
        data_key="counterfactual_status",
        name="No-Action Counterfactual Status",
    ),
    _soc(
        "counterfactual_soc",
        "counterfactual_soc_pct",
        "No-Action Counterfactual SOC",
    ),
    _energy(
        "counterfactual_stored_energy",
        "counterfactual_stored_kwh",
        "No-Action Counterfactual Stored Energy",
        storage=True,
    ),
    _energy(
        "counterfactual_headroom",
        "counterfactual_headroom_kwh",
        "No-Action Counterfactual Headroom",
        storage=True,
    ),
    _soc(
        "counterfactual_projected_sunset_soc",
        "counterfactual_projected_sunset_soc_pct",
        "No-Action Projected Sunset SOC",
    ),
    _energy(
        "counterfactual_projected_export",
        "counterfactual_projected_export_kwh",
        "No-Action Projected Export",
    ),
    _energy(
        "counterfactual_preserved_headroom",
        "counterfactual_preserved_headroom_kwh",
        "EV Preserved Battery Headroom Today",
        storage=True,
    ),
    _energy(
        "counterfactual_avoided_export",
        "counterfactual_avoided_export_kwh",
        "Avoided Solar Export Today",
    ),
    _energy(
        "ev_wall_energy_today",
        "ev_wall_energy_today_kwh",
        "EV Wall Energy Today",
    ),
    _energy(
        "ev_solar_energy_today",
        "ev_solar_energy_today_kwh",
        "EV Solar Energy Today",
    ),
    _percent(
        "ev_solar_fraction_today",
        "ev_solar_fraction_today_pct",
        "EV Solar Fraction Today",
    ),
    EnergyPlannerSensorDescription(
        key="live_solar_surplus",
        data_key="live_solar_surplus_w",
        name="Live Solar Surplus",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    _energy(
        "live_solar_capture_recommended_energy",
        "live_solar_capture_recommended_energy_kwh",
        "Live Solar Capture Recommended EV Energy",
    ),

    # Rolling paid-forecast EV/headroom advisory. This remains advisory-only.
    EnergyPlannerSensorDescription(
        key="rolling_ev_status",
        data_key="rolling_ev_status",
        name="Rolling EV Advisory Status",
    ),
    EnergyPlannerSensorDescription(
        key="rolling_planning_base_load_w",
        data_key="rolling_planning_base_load_w",
        name="Rolling Planning Base Load",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="rolling_planning_base_load_source",
        data_key="rolling_planning_base_load_source",
        name="Rolling Planning Base Load Source",
    ),
    EnergyPlannerSensorDescription(
        key="rolling_headroom_risk_date",
        data_key="rolling_headroom_risk_date",
        name="Rolling Headroom Risk Date",
    ),
    _energy(
        "rolling_headroom_shortfall_kwh",
        "rolling_headroom_shortfall_kwh",
        "Rolling Headroom Shortfall",
    ),
    _soc(
        "rolling_risk_projected_soc",
        "rolling_risk_projected_soc",
        "Rolling Risk Day Projected Sunset SOC",
    ),
    _energy(
        "rolling_risk_export_kwh",
        "rolling_risk_export_kwh",
        "Rolling Risk Day Capacity Export",
    ),
    _energy(
        "rolling_ev_available_energy_kwh",
        "rolling_ev_available_energy_kwh",
        "EV Available Energy to Target",
    ),
    EnergyPlannerSensorDescription(
        key="rolling_ev_charge_power_w",
        data_key="rolling_ev_charge_power_w",
        name="EV Charge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="rolling_ev_charge_power_samples",
        data_key="rolling_ev_charge_power_samples",
        name="EV Charge Power Samples",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EnergyPlannerSensorDescription(
        key="rolling_ev_charge_power_source",
        data_key="rolling_ev_charge_power_source",
        name="EV Charge Power Source",
    ),
    _energy(
        "rolling_ev_wall_full_kwh",
        "rolling_ev_wall_full_kwh",
        "EV Learned Full-Range Wall Energy",
    ),
    EnergyPlannerSensorDescription(
        key="rolling_ev_wall_full_samples",
        data_key="rolling_ev_wall_full_samples",
        name="EV Wall-Energy Learning Samples",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EnergyPlannerSensorDescription(
        key="rolling_ev_wall_full_source",
        data_key="rolling_ev_wall_full_source",
        name="EV Wall-Energy Estimate Source",
    ),
    _energy(
        "rolling_ev_recommended_energy_kwh",
        "rolling_ev_recommended_energy_kwh",
        "Recommended EV Charge Energy",
    ),
    EnergyPlannerSensorDescription(
        key="rolling_ev_window_start",
        data_key="rolling_ev_window_start",
        name="Recommended EV Charge Window Start",
    ),
    EnergyPlannerSensorDescription(
        key="rolling_ev_window_end",
        data_key="rolling_ev_window_end",
        name="Recommended EV Charge Window End",
    ),
    _energy(
        "rolling_ev_window_solar_kwh",
        "rolling_ev_window_solar_kwh",
        "Recommended EV Window Solar Energy",
    ),
    _energy(
        "rolling_ev_window_grid_kwh",
        "rolling_ev_window_grid_kwh",
        "Recommended EV Window Grid Energy",
    ),
    _energy(
        "rolling_ev_headroom_preserved_kwh",
        "rolling_ev_headroom_preserved_kwh",
        "Recommended EV Preserved Battery Headroom",
    ),
    _energy(
        "rolling_ev_residual_headroom_shortfall_kwh",
        "rolling_ev_residual_headroom_shortfall_kwh",
        "Residual Headroom Shortfall After EV Charge",
    ),
    _energy(
        "rolling_ev_residual_capacity_export_kwh",
        "rolling_ev_residual_capacity_export_kwh",
        "Residual Capacity Export After EV Charge",
    ),
    EnergyPlannerSensorDescription(
        key="rolling_ev_model",
        data_key="rolling_ev_model",
        name="Rolling EV Forecast Model",
    ),

    # Forecast-calibration observability.
    EnergyPlannerSensorDescription(
        key="calibration_status",
        data_key="calibration_status",
        name="Forecast Calibration Status",
    ),
    EnergyPlannerSensorDescription(
        key="calibration_action_ready",
        data_key="calibration_action_ready",
        name="Confidence Headroom Action Ready",
    ),
    EnergyPlannerSensorDescription(
        key="calibration_daylight_samples",
        data_key="calibration_daylight_samples",
        name="Daylight Calibration Samples",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EnergyPlannerSensorDescription(
        key="calibration_overnight_samples",
        data_key="calibration_overnight_samples",
        name="Overnight Calibration Samples",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _energy(
        "calibration_daylight_mae_kwh",
        "calibration_daylight_mae_kwh",
        "Daylight Stored-Energy Forecast MAE",
    ),
    _percent(
        "calibration_daylight_mae_ratio_pct",
        "calibration_daylight_mae_ratio_pct",
        "Daylight Stored-Energy Forecast MAPE",
    ),
    _percent(
        "calibration_headroom_factor_pct",
        "calibration_headroom_factor_pct",
        "No-Regret Headroom Factor",
    ),
    EnergyPlannerSensorDescription(
        key="calibration_overnight_median_kw",
        data_key="calibration_overnight_median_kw",
        name="Median Overnight Battery Depletion Rate",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="calibration_overnight_upper_kw",
        data_key="calibration_overnight_upper_kw",
        name="No-Regret Overnight Battery Depletion Rate",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),

    EnergyPlannerSensorDescription(
        key="today_plan_date", data_key="today_plan_date", name="Today Plan Date"
    ),
    EnergyPlannerSensorDescription(
        key="today_plan_ready", data_key="today_plan_ready", name="Today Plan Ready"
    ),
    EnergyPlannerSensorDescription(
        key="today_strategy", data_key="today_strategy", name="Today Strategy"
    ),
    EnergyPlannerSensorDescription(
        key="today_strategy_reason",
        data_key="today_strategy_reason",
        name="Today Strategy Reason",
    ),
    _soc("today_projected_max_soc", "today_projected_max_soc", "Projected Maximum SOC Today"),
    _soc(
        "today_projected_sunset_soc",
        "today_projected_sunset_soc",
        "Projected Sunset SOC Today",
    ),
    _energy("today_baseline_export", "today_baseline_export", "Baseline Unmitigated Export Today"),
    _energy(
        "today_baseline_capacity_export",
        "today_baseline_capacity_export",
        "Baseline Capacity-Limited Export Today",
    ),
    _energy("today_predicted_export", "today_predicted_export", "Planned Solar Export Today"),
    _energy(
        "today_predicted_grid_import",
        "today_predicted_grid_import",
        "Predicted Daylight Grid Import Today",
    ),
    _energy(
        "today_discretionary_energy",
        "today_discretionary_energy",
        "Discretionary Energy Today",
    ),
    _energy(
        "today_recommended_presolar_discharge",
        "today_recommended_presolar_discharge",
        "Confidence-Adjusted Pre-Solar Discharge Today",
    ),
    _energy(
        "today_capacity_limited_export",
        "today_capacity_limited_export",
        "Planned Capacity-Limited Export Today",
    ),
    _energy(
        "today_power_limited_export",
        "today_power_limited_export",
        "Planned Power-Limited Export Today",
    ),
    _energy(
        "today_control_limited_export",
        "today_control_limited_export",
        "Control-Unavailable Export Today",
    ),
    _energy(
        "today_grid_to_battery",
        "today_grid_to_battery",
        "Forecast Grid-to-Battery Today",
    ),
    EnergyPlannerSensorDescription(
        key="today_peak_export_w",
        data_key="today_peak_export_w",
        name="Peak Unavoidable Export Today",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="today_export_minutes",
        data_key="today_export_minutes",
        name="Unavoidable Export Duration Today",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="today_projection_model",
        data_key="today_projection_model",
        name="Today Projection Model",
    ),

    EnergyPlannerSensorDescription(
        key="next_day_plan_date",
        data_key="next_day_plan_date",
        name="Next Day Plan Date",
    ),
    EnergyPlannerSensorDescription(
        key="tomorrow_plan_ready",
        data_key="tomorrow_plan_ready",
        name="Next Day Plan Ready",
    ),
    EnergyPlannerSensorDescription(
        key="tomorrow_strategy",
        data_key="tomorrow_strategy",
        name="Next Day Strategy",
    ),
    EnergyPlannerSensorDescription(
        key="tomorrow_strategy_reason",
        data_key="tomorrow_strategy_reason",
        name="Next Day Strategy Reason",
    ),
    _energy(
        "required_headroom_tomorrow",
        "required_headroom_tomorrow",
        "Nominal Required Headroom Next Day",
    ),
    _energy(
        "headroom_margin_tomorrow",
        "headroom_margin_tomorrow",
        "Nominal Headroom Margin Next Day",
    ),
    _energy(
        "headroom_shortfall_tomorrow",
        "headroom_shortfall_tomorrow",
        "Nominal Headroom Shortfall Next Day",
    ),
    _energy(
        "confidence_required_headroom_tomorrow",
        "confidence_required_headroom_tomorrow",
        "No-Regret Required Headroom Next Day",
    ),
    _energy(
        "confidence_available_headroom_tomorrow",
        "confidence_available_headroom_tomorrow",
        "No-Regret Available Headroom Next Day",
    ),
    _energy(
        "confidence_headroom_shortfall_tomorrow",
        "confidence_headroom_shortfall_tomorrow",
        "No-Regret Headroom Shortfall Next Day",
    ),
    _energy(
        "recommended_overnight_discharge",
        "recommended_overnight_discharge",
        "Confidence-Adjusted Discharge Before Next Day",
    ),
    _energy(
        "nominal_overnight_drop_tomorrow",
        "nominal_overnight_drop_tomorrow",
        "Expected Natural Overnight Battery Depletion",
    ),
    _energy(
        "no_regret_overnight_drop_tomorrow",
        "no_regret_overnight_drop_tomorrow",
        "No-Regret Natural Overnight Headroom Allowance",
    ),
    _soc(
        "projected_next_day_start_soc",
        "projected_next_day_start_soc",
        "Projected Natural Start SOC Next Day",
    ),
    _soc(
        "planned_next_day_start_soc",
        "planned_next_day_start_soc",
        "Planned Start SOC Next Day",
    ),
    _soc(
        "projected_max_soc_tomorrow",
        "projected_max_soc_tomorrow",
        "Projected Maximum SOC Next Day",
    ),
    _soc(
        "projected_sunset_soc_tomorrow",
        "projected_sunset_soc_tomorrow",
        "Projected Sunset SOC Next Day",
    ),
    _energy(
        "baseline_export_tomorrow",
        "baseline_export_tomorrow",
        "Baseline Unmitigated Export Next Day",
    ),
    _energy(
        "baseline_capacity_export_tomorrow",
        "baseline_capacity_export_tomorrow",
        "Baseline Capacity-Limited Export Next Day",
    ),
    _energy(
        "baseline_power_export_tomorrow",
        "baseline_power_export_tomorrow",
        "Baseline Power-Limited Export Next Day",
    ),
    _energy(
        "baseline_control_export_tomorrow",
        "baseline_control_export_tomorrow",
        "Baseline Control-Unavailable Export Next Day",
    ),
    _energy(
        "predicted_export_tomorrow",
        "predicted_export_tomorrow",
        "Planned Solar Export Next Day",
    ),
    _energy(
        "predicted_grid_import_tomorrow",
        "predicted_grid_import_tomorrow",
        "Predicted Daylight Grid Import Next Day",
    ),
    _energy(
        "discretionary_energy_tomorrow",
        "discretionary_energy_tomorrow",
        "Discretionary Energy Next Day",
    ),
    _energy(
        "next_day_capacity_limited_export",
        "next_day_capacity_limited_export",
        "Planned Capacity-Limited Export Next Day",
    ),
    _energy(
        "next_day_power_limited_export",
        "next_day_power_limited_export",
        "Planned Power-Limited Export Next Day",
    ),
    _energy(
        "next_day_control_limited_export",
        "next_day_control_limited_export",
        "Planned Control-Unavailable Export Next Day",
    ),
    _energy(
        "next_day_grid_to_battery",
        "next_day_grid_to_battery",
        "Forecast Grid-to-Battery Next Day",
    ),
    EnergyPlannerSensorDescription(
        key="next_day_peak_export_w",
        data_key="next_day_peak_export_w",
        name="Peak Unavoidable Export Next Day",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="next_day_export_minutes",
        data_key="next_day_export_minutes",
        name="Unavoidable Export Duration Next Day",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    EnergyPlannerSensorDescription(
        key="tomorrow_projection_model",
        data_key="tomorrow_projection_model",
        name="Next Day Projection Model",
    ),

    EnergyPlannerSensorDescription(
        key="controller_model_source",
        data_key="controller_model_source",
        name="Forecast Capture Source",
    ),
    EnergyPlannerSensorDescription(
        key="controller_model_enabled",
        data_key="controller_model_enabled",
        name="Forecast Capture Available",
    ),
    _percent(
        "effective_reserve_floor",
        "effective_reserve_floor",
        "Effective Reserve Floor",
    ),
    _percent("reserve", "reserve", "Current Backup Reserve"),
    _soc("ev_soc", "ev_soc", "EV SOC"),
    EnergyPlannerSensorDescription(key="ev_plan", data_key="ev_plan", name="EV Charge Plan"),
    EnergyPlannerSensorDescription(
        key="control_ready", data_key="control_ready", name="Control Ready"
    ),
    EnergyPlannerSensorDescription(
        key="headroom_release",
        data_key="headroom_release",
        name="Headroom Release Requested",
    ),
)


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: EnergyPlannerCoordinator = entry.runtime_data
    async_add_entities(
        EnergyPlannerSensor(coordinator, entry, description)
        for description in SENSORS
    )


class EnergyPlannerSensor(CoordinatorEntity[EnergyPlannerCoordinator], SensorEntity):
    entity_description: EnergyPlannerSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyPlannerCoordinator,
        entry: ConfigEntry,
        description: EnergyPlannerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {("energy_planner", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Community",
            "model": "Energy Planner",
        }

    @property
    def native_value(self) -> Any:
        value = (self.coordinator.data or {}).get(self.entity_description.data_key)
        if isinstance(value, float):
            return round(value, 2)
        if isinstance(value, bool):
            return "On" if value else "Off"
        return value

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        if self.entity_description.key == "solar_learning_status":
            return data.get("solar_learning_diagnostics", {})
        if self.entity_description.key == "hvac_daily_electricity":
            return {
                **data.get("hvac_energy_coverage", {}),
                "power_sources": data.get("hvac_power_sources", {}),
                "power_mapping_valid": data.get("hvac_power_mapping_valid"),
            }
        if self.entity_description.key == "hvac_electrical_power":
            return {
                "power_sources": data.get("hvac_power_sources", {}),
                "power_mapping_valid": data.get("hvac_power_mapping_valid"),
                "validity_policy": (
                    "numeric available Home Assistant state; unchanged values remain "
                    "valid until HA marks the source unknown/unavailable"
                ),
            }
        if self.entity_description.key == "hvac_status":
            return data.get("hvac_diagnostics", {})
        if self.entity_description.key == "storm_safety_status":
            return {
                "entity_id": data.get("storm_warning_entity"),
                "raw_state": data.get("storm_warning_state"),
                "active": data.get("storm"),
            }
        if self.entity_description.key == "battery_topology_source":
            return {
                "reason": data.get("battery_topology_reason"),
                "effective_capacity_kwh": data.get("battery_capacity_kwh"),
                "configured_capacity_kwh": data.get(
                    "battery_configured_capacity_kwh"
                ),
                "pack_counts": data.get("battery_pack_counts"),
                "pack_count_total": data.get("battery_pack_count_total"),
                "bank_socs_pct": data.get("battery_bank_socs_pct"),
                "bank_capacities_kwh": data.get(
                    "battery_bank_capacities_kwh"
                ),
                "discovered_dpu_count": data.get("battery_discovered_dpu_count"),
            }
        if self.entity_description.key == "forecast_learning_progress":
            return {
                "ready": data.get("forecast_learning_ready"),
                "scored_sunset_samples": data.get("forecast_learning_sunset_samples", 0),
                "scored_sunset_samples_required": data.get(
                    "forecast_learning_sunset_required", 3
                ),
                "overnight_records": data.get("forecast_learning_overnight_samples", 0),
                "overnight_records_required": data.get(
                    "forecast_learning_overnight_required", 3
                ),
            }
        if self.entity_description.key not in {
            "forecast_confidence",
            "forecast_reliability_status",
        }:
            return None
        return {
            "reason": data.get("forecast_reliability_reason"),
            "historical_mae_soc_percentage_points": data.get(
                "forecast_historical_mae_soc"
            ),
            "historical_mae_kwh": data.get("forecast_historical_mae_kwh"),
            "historical_signed_bias_kwh": data.get(
                "forecast_historical_signed_bias_kwh"
            ),
            "export_underprediction_bias_kwh": data.get(
                "forecast_export_underprediction_bias_kwh"
            ),
            "reliability_record_schema": data.get(
                "forecast_reliability_record_schema"
            ),
            "reliability_record_count": data.get(
                "forecast_reliability_record_count"
            ),
            "migrated_reliability_records": data.get(
                "forecast_reliability_migrated_records"
            ),
            "inferred_capacity_records": data.get(
                "forecast_reliability_inferred_capacity_records"
            ),
            "topology_rebased_at": data.get("forecast_learning_rebased_at"),
            "topology_rebase_reason": data.get(
                "forecast_learning_rebased_reason"
            ),
            "topology_rebase_preserved_samples": data.get(
                "forecast_learning_preserved_samples"
            ),
            "topology_rebase_discarded_pending": data.get(
                "forecast_learning_discarded_pending"
            ),
            "error_samples": data.get("forecast_error_samples", 0),
            "confirmed_refreshes": data.get("forecast_confirmation_count", 0),
            "provider_refreshed_at": data.get("forecast_revision_at"),
            "forecast_learning_ready": data.get("forecast_learning_ready"),
            "forecast_learning_progress": data.get("forecast_learning_progress"),
            "forecast_learning_sunset_samples": data.get(
                "forecast_learning_sunset_samples", 0
            ),
            "forecast_learning_sunset_required": data.get(
                "forecast_learning_sunset_required", 3
            ),
            "forecast_learning_overnight_samples": data.get(
                "forecast_learning_overnight_samples", 0
            ),
            "forecast_learning_overnight_required": data.get(
                "forecast_learning_overnight_required", 3
            ),
            "storm_safety_status": data.get("storm_safety_status"),
            "storm_warning_entity": data.get("storm_warning_entity"),
            "storm_warning_state": data.get("storm_warning_state"),
            "days": data.get("rolling_day_plans", []),
            "range_kind": "scenario envelope; not a probability interval",
        }
