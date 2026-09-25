from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .v018_coordinator import EnergyPlannerV018Coordinator


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: EnergyPlannerV018Coordinator = entry.runtime_data
    async_add_entities(
        [
            EnergyPlannerHeadroomRisk(coordinator, entry),
            EnergyPlannerDynamicLoadForecast(coordinator, entry),
            EnergyPlannerAutoChargeEligible(coordinator, entry),
            EnergyPlannerForecastExportRisk(coordinator, entry),
            EnergyPlannerCounterfactualHeadroomRisk(coordinator, entry),
            EnergyPlannerLiveSolarCaptureOpportunity(coordinator, entry),
            EnergyPlannerNextSunsetAvailable(coordinator, entry),
        ]
    )


class EnergyPlannerHeadroomRisk(
    CoordinatorEntity[EnergyPlannerV018Coordinator], BinarySensorEntity
):
    _attr_has_entity_name = True
    _attr_name = "Headroom Risk Today"

    def __init__(self, coordinator: EnergyPlannerV018Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_headroom_risk_today"
        self._attr_device_info = {
            "identifiers": {("energy_planner", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Community",
            "model": "Energy Planner",
        }

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("authoritative_headroom_risk", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data or {}
        return {
            "status": data.get("authoritative_headroom_status"),
            "risk_date": data.get("authoritative_headroom_risk_date"),
            "shortfall_kwh": data.get("authoritative_headroom_shortfall_kwh"),
            "capacity_export_kwh": data.get("authoritative_headroom_capacity_export_kwh"),
            "action": data.get("authoritative_headroom_action"),
            "forecast_source": data.get("rolling_current_day_forecast_source"),
            "current_day_scale_factor": data.get("rolling_current_day_scale_factor"),
            "raw_remaining_kwh": data.get("rolling_current_day_raw_remaining_kwh"),
            "corrected_remaining_kwh": data.get("rolling_current_day_corrected_remaining_kwh"),
        }


class EnergyPlannerDynamicLoadForecast(
    CoordinatorEntity[EnergyPlannerV018Coordinator], BinarySensorEntity
):
    """Expose the complete modeled horizon and whether any day needs flexible load."""

    _attr_has_entity_name = True
    _attr_name = "Dynamic Load Forecast"

    def __init__(self, coordinator: EnergyPlannerV018Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_dynamic_load_forecast"
        self._attr_device_info = {
            "identifiers": {("energy_planner", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Community",
            "model": "Energy Planner",
        }

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("rolling_dynamic_load_days_count", 0))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data or {}
        return {
            "status": data.get("rolling_dynamic_load_forecast_status"),
            "battery_outlook_status": data.get("battery_outlook_status"),
            "battery_outlook_reason": data.get("battery_outlook_reason"),
            "battery_outlook_inputs": data.get("battery_outlook_inputs", {}),
            "days": data.get("rolling_day_plans", []),
            "risk_dates": data.get("rolling_dynamic_load_risk_dates", []),
            "risk_days_count": data.get("rolling_dynamic_load_days_count", 0),
            "dynamic_load_next_3d_kwh": data.get("rolling_dynamic_load_next_3d_kwh", 0.0),
            "dynamic_load_horizon_kwh": data.get("rolling_dynamic_load_total_kwh", 0.0),
            "model": data.get("rolling_dynamic_load_forecast_model"),
        }


class EnergyPlannerAutoChargeEligible(
    CoordinatorEntity[EnergyPlannerV018Coordinator], BinarySensorEntity
):
    _attr_has_entity_name = True
    _attr_name = "EV Auto-Charge Eligible"

    def __init__(self, coordinator: EnergyPlannerV018Coordinator, entry: ConfigEntry) -> None:
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
            "learned_charge_power_w": data.get("rolling_ev_charge_power_w"),
            "learned_charge_power_source": data.get("rolling_ev_charge_power_source"),
            "recommended_energy_kwh": data.get("rolling_ev_recommended_energy_kwh"),
            "headroom_preserved_kwh": data.get("rolling_ev_headroom_preserved_kwh"),
            "surplus_next_3d_kwh": data.get("rolling_ev_surplus_next_3d_kwh"),
            "surplus_horizon_kwh": data.get("rolling_ev_surplus_horizon_kwh"),
        }


class EnergyPlannerForecastExportRisk(
    CoordinatorEntity[EnergyPlannerV018Coordinator], BinarySensorEntity
):
    """Forecast risk that solar will exhaust stationary-battery headroom."""

    _attr_has_entity_name = True
    _attr_name = "Forecast Export Risk"

    def __init__(self, coordinator: EnergyPlannerV018Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_forecast_export_risk"
        self._attr_device_info = {
            "identifiers": {("energy_planner", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Community",
            "model": "Energy Planner",
        }

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("forecast_export_risk", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data or {}
        return {
            "risk_date": data.get("forecast_export_risk_date"),
            "headroom_kwh": data.get("forecast_export_headroom_kwh"),
            "flexible_load_kwh": data.get("forecast_export_wall_energy_kwh"),
            "reason": data.get("forecast_export_risk_reason"),
            "risk_days": data.get("forecast_export_risk_days"),
            "model": data.get("forecast_export_model"),
            "objective": data.get("forecast_objective"),
            "reliability_status": data.get("forecast_reliability_status"),
        }


class EnergyPlannerCounterfactualHeadroomRisk(
    CoordinatorEntity[EnergyPlannerV018Coordinator], BinarySensorEntity
):
    """Risk that would exist today without discretionary solar diversion."""

    _attr_has_entity_name = True
    _attr_name = "No-Action Headroom Risk Today"

    def __init__(self, coordinator: EnergyPlannerV018Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_counterfactual_headroom_risk_today"
        self._attr_device_info = {
            "identifiers": {("energy_planner", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Community",
            "model": "Energy Planner",
        }

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("counterfactual_risk_today", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data or {}
        return {
            "source": data.get("counterfactual_risk_source"),
            "current_soc_pct": data.get("counterfactual_soc_pct"),
            "projected_sunset_soc_pct": data.get(
                "counterfactual_projected_sunset_soc_pct"
            ),
            "headroom_kwh": data.get("counterfactual_headroom_kwh"),
            "projected_export_kwh": data.get("counterfactual_projected_export_kwh"),
            "live_implied_export_kwh": data.get(
                "counterfactual_live_implied_export_kwh"
            ),
            "fill_hours": data.get("counterfactual_live_fill_hours"),
            "remaining_daylight_hours": data.get(
                "counterfactual_remaining_daylight_hours"
            ),
            "ev_solar_kwh_today": data.get("ev_solar_energy_today_kwh"),
            "preserved_headroom_kwh": data.get(
                "counterfactual_preserved_headroom_kwh"
            ),
            "avoided_export_kwh": data.get("counterfactual_avoided_export_kwh"),
        }


class EnergyPlannerLiveSolarCaptureOpportunity(
    CoordinatorEntity[EnergyPlannerV018Coordinator], BinarySensorEntity
):
    """Live no-regret EV charging opportunity independent of forecast stability."""

    _attr_has_entity_name = True
    _attr_name = "Live Solar Capture Opportunity"

    def __init__(self, coordinator: EnergyPlannerV018Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_live_solar_capture_opportunity"
        self._attr_device_info = {
            "identifiers": {("energy_planner", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Community",
            "model": "Energy Planner",
        }

    @property
    def is_on(self) -> bool:
        return bool(
            (self.coordinator.data or {}).get(
                "live_solar_capture_opportunity",
                False,
            )
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data or {}
        return {
            "status": data.get("live_solar_capture_status"),
            "reason": data.get("live_solar_capture_reason"),
            "recommended_energy_kwh": data.get(
                "live_solar_capture_recommended_energy_kwh"
            ),
            "live_solar_surplus_w": data.get("live_solar_surplus_w"),
            "counterfactual_soc_pct": data.get("counterfactual_soc_pct"),
            "counterfactual_headroom_kwh": data.get(
                "counterfactual_headroom_kwh"
            ),
            "counterfactual_projected_sunset_soc_pct": data.get(
                "counterfactual_projected_sunset_soc_pct"
            ),
            "counterfactual_projected_export_kwh": data.get(
                "counterfactual_projected_export_kwh"
            ),
            "ev_wall_kwh_today": data.get("ev_wall_energy_today_kwh"),
            "ev_solar_kwh_today": data.get("ev_solar_energy_today_kwh"),
            "preserved_headroom_kwh": data.get(
                "counterfactual_preserved_headroom_kwh"
            ),
            "avoided_export_kwh": data.get("counterfactual_avoided_export_kwh"),
        }


class EnergyPlannerNextSunsetAvailable(
    CoordinatorEntity[EnergyPlannerV018Coordinator], BinarySensorEntity
):
    """Expose stable next-future-sunset forecast values as dashboard attributes."""

    _attr_has_entity_name = True
    _attr_name = "Next Sunset Forecast Available"

    def __init__(self, coordinator: EnergyPlannerV018Coordinator, entry: ConfigEntry) -> None:
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
            "soc_low_pct": next((r.get("sunset_soc_low_pct") for r in data.get("rolling_day_plans", []) if r["date"] == data.get("next_sunset_date")), None),
            "soc_high_pct": next((r.get("sunset_soc_high_pct") for r in data.get("rolling_day_plans", []) if r["date"] == data.get("next_sunset_date")), None),
            "expected_charge_kwh": data.get("next_sunset_expected_charge"),
            "expected_grid_import_kwh": data.get("next_sunset_expected_grid_import"),
            "expected_export_kwh": data.get("next_sunset_expected_export"),
            "forecast_source": data.get("next_sunset_forecast_source"),
        }
