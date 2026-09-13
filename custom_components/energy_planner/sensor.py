from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EnergyPlannerCoordinator


@dataclass(frozen=True, kw_only=True)
class EnergyPlannerSensorDescription(SensorEntityDescription):
    data_key: str


SENSORS = (
    EnergyPlannerSensorDescription(
        key="weighted_soc",
        data_key="weighted_soc",
        name="Whole Bank SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EnergyPlannerSensorDescription(
        key="stored_energy",
        data_key="stored_energy",
        name="Whole Bank Stored Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EnergyPlannerSensorDescription(
        key="battery_headroom",
        data_key="battery_headroom",
        name="Battery Headroom",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EnergyPlannerSensorDescription(
        key="upcoming_solar",
        data_key="upcoming_solar",
        name="Upcoming Solar",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
    ),
    EnergyPlannerSensorDescription(
        key="projected_sunset_soc",
        data_key="projected_sunset_soc",
        name="Live Projected Sunset SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="projected_charge_to_sunset",
        data_key="projected_charge_to_sunset",
        name="Live Projected Battery Charge to Sunset",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="projection_available_ac",
        data_key="projection_available_ac",
        name="Live Projected Charge Opportunity to Sunset",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="projection_model",
        data_key="projection_model",
        name="Live Sunset Projection Model",
    ),
    EnergyPlannerSensorDescription(
        key="today_plan_date",
        data_key="today_plan_date",
        name="Today Plan Date",
    ),
    EnergyPlannerSensorDescription(
        key="today_plan_ready",
        data_key="today_plan_ready",
        name="Today Plan Ready",
    ),
    EnergyPlannerSensorDescription(
        key="today_strategy",
        data_key="today_strategy",
        name="Today Strategy",
    ),
    EnergyPlannerSensorDescription(
        key="today_strategy_reason",
        data_key="today_strategy_reason",
        name="Today Strategy Reason",
    ),
    EnergyPlannerSensorDescription(
        key="today_projected_max_soc",
        data_key="today_projected_max_soc",
        name="Projected Maximum SOC Today",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="today_projected_sunset_soc",
        data_key="today_projected_sunset_soc",
        name="Projected Sunset SOC Today",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="today_predicted_export",
        data_key="today_predicted_export",
        name="Predicted Solar Export Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="today_predicted_grid_import",
        data_key="today_predicted_grid_import",
        name="Predicted Daylight Grid Import Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="today_discretionary_energy",
        data_key="today_discretionary_energy",
        name="Discretionary Energy Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="today_recommended_presolar_discharge",
        data_key="today_recommended_presolar_discharge",
        name="Recommended Pre-Solar Discharge Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
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
    EnergyPlannerSensorDescription(
        key="required_headroom_tomorrow",
        data_key="required_headroom_tomorrow",
        name="Required Headroom Next Day",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="headroom_margin_tomorrow",
        data_key="headroom_margin_tomorrow",
        name="Headroom Margin Next Day",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="headroom_shortfall_tomorrow",
        data_key="headroom_shortfall_tomorrow",
        name="Headroom Shortfall Next Day",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="recommended_overnight_discharge",
        data_key="recommended_overnight_discharge",
        name="Recommended Discharge Before Next Day",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="projected_next_day_start_soc",
        data_key="projected_next_day_start_soc",
        name="Projected Start SOC Next Day",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="planned_next_day_start_soc",
        data_key="planned_next_day_start_soc",
        name="Planned Start SOC Next Day",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="projected_max_soc_tomorrow",
        data_key="projected_max_soc_tomorrow",
        name="Projected Maximum SOC Next Day",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="projected_sunset_soc_tomorrow",
        data_key="projected_sunset_soc_tomorrow",
        name="Projected Sunset SOC Next Day",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="predicted_export_tomorrow",
        data_key="predicted_export_tomorrow",
        name="Predicted Solar Export Next Day",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="predicted_grid_import_tomorrow",
        data_key="predicted_grid_import_tomorrow",
        name="Predicted Daylight Grid Import Next Day",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="discretionary_energy_tomorrow",
        data_key="discretionary_energy_tomorrow",
        name="Discretionary Energy Next Day",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="tomorrow_projection_model",
        data_key="tomorrow_projection_model",
        name="Next Day Projection Model",
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
    EnergyPlannerSensorDescription(
        key="ev_plan",
        data_key="ev_plan",
        name="EV Charge Plan",
    ),
    EnergyPlannerSensorDescription(
        key="control_ready",
        data_key="control_ready",
        name="Control Ready",
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
