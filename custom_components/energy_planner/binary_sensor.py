from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .v014_coordinator import EnergyPlannerV014Coordinator


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: EnergyPlannerV014Coordinator = entry.runtime_data
    async_add_entities(
        [
            EnergyPlannerAutoChargeEligible(coordinator, entry),
            EnergyPlannerNextSunsetAvailable(coordinator, entry),
        ]
    )


class EnergyPlannerAutoChargeEligible(
    CoordinatorEntity[EnergyPlannerV014Coordinator], BinarySensorEntity
):
    _attr_has_entity_name = True
    _attr_name = "EV Auto-Charge Eligible"

    def __init__(self, coordinator: EnergyPlannerV014Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ev_auto_charge_eligible"
        self._attr_device_info = {
            "identifiers": {("energy_planner", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Community",
            "model": "Energy Planner",
        }

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("rolling_ev_auto_charge_eligible", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data or {}
        return {
            "reason": data.get("rolling_ev_auto_charge_reason"),
            "charging_status": data.get("rolling_ev_status"),
            "advisory_detail": data.get("rolling_ev_advisory_detail"),
            "preferred_window_start": data.get("rolling_ev_window_start"),
            "preferred_window_end": data.get("rolling_ev_window_end"),
            "preferred_window_solar_fraction_pct": data.get(
                "rolling_ev_window_solar_fraction_pct"
            ),
            "ev_soc_data_status": data.get("rolling_ev_soc_data_status"),
            "ev_soc_age_minutes": data.get("rolling_ev_soc_age_minutes"),
            "ev_learning_status": data.get("rolling_ev_learning_status"),
            "ev_power_entity": data.get("rolling_ev_power_entity"),
            "ev_current_power_w": data.get("rolling_ev_current_power_w"),
            "surplus_next_3d_kwh": data.get("rolling_ev_surplus_next_3d_kwh"),
            "surplus_horizon_kwh": data.get("rolling_ev_surplus_horizon_kwh"),
        }


class EnergyPlannerNextSunsetAvailable(
    CoordinatorEntity[EnergyPlannerV014Coordinator], BinarySensorEntity
):
    """Expose stable next-future-sunset forecast values as dashboard attributes."""

    _attr_has_entity_name = True
    _attr_name = "Next Sunset Forecast Available"

    def __init__(self, coordinator: EnergyPlannerV014Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_next_sunset_forecast_available"
        self._attr_device_info = {
            "identifiers": {("energy_planner", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Community",
            "model": "Energy Planner",
        }

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}
        return data.get("next_sunset_date") is not None and data.get("next_sunset_soc") is not None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data or {}
        return {
            "date": data.get("next_sunset_date"),
            "soc_pct": data.get("next_sunset_soc"),
            "expected_charge_kwh": data.get("next_sunset_expected_charge"),
            "expected_grid_import_kwh": data.get("next_sunset_expected_grid_import"),
            "expected_export_kwh": data.get("next_sunset_expected_export"),
            "forecast_source": data.get("next_sunset_forecast_source"),
        }
