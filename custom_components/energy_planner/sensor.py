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
        name="Projected Sunset SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EnergyPlannerSensorDescription(
        key="projected_charge_to_sunset",
        data_key="projected_charge_to_sunset",
        name="Projected Battery Charge to Sunset",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="projection_available_ac",
        data_key="projection_available_ac",
        name="Projected Charge Opportunity to Sunset",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EnergyPlannerSensorDescription(
        key="projection_model",
        data_key="projection_model",
        name="Sunset Projection Model",
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
