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
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTime
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


SENSORS = (
    _soc("weighted_soc", "weighted_soc", "Whole Bank SOC"),
    _energy("stored_energy", "stored_energy", "Whole Bank Stored Energy", storage=True),
    _energy("battery_headroom", "battery_headroom", "Battery Headroom", storage=True),
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
    _energy("today_predicted_export", "today_predicted_export", "Predicted Solar Export Today"),
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
        "Recommended Pre-Solar Discharge Today",
    ),
    _energy(
        "today_capacity_limited_export",
        "today_capacity_limited_export",
        "Capacity-Limited Export Today",
    ),
    _energy(
        "today_power_limited_export",
        "today_power_limited_export",
        "Power-Limited Export Today",
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
        "Required Headroom Next Day",
    ),
    _energy(
        "headroom_margin_tomorrow",
        "headroom_margin_tomorrow",
        "Headroom Margin Next Day",
    ),
    _energy(
        "headroom_shortfall_tomorrow",
        "headroom_shortfall_tomorrow",
        "Headroom Shortfall Next Day",
    ),
    _energy(
        "recommended_overnight_discharge",
        "recommended_overnight_discharge",
        "Recommended Discharge Before Next Day",
    ),
    _soc(
        "projected_next_day_start_soc",
        "projected_next_day_start_soc",
        "Projected Start SOC Next Day",
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
        "predicted_export_tomorrow",
        "predicted_export_tomorrow",
        "Predicted Solar Export Next Day",
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
        "Capacity-Limited Export Next Day",
    ),
    _energy(
        "next_day_power_limited_export",
        "next_day_power_limited_export",
        "Power-Limited Export Next Day",
    ),
    _energy(
        "next_day_control_limited_export",
        "next_day_control_limited_export",
        "Control-Unavailable Export Next Day",
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
    EnergyPlannerSensorDescription(
        key="effective_reserve_floor",
        data_key="effective_reserve_floor",
        name="Effective Reserve Floor",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="reserve",
        data_key="reserve",
        name="Current Backup Reserve",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EnergyPlannerSensorDescription(
        key="ev_soc",
        data_key="ev_soc",
        name="EV SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
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
