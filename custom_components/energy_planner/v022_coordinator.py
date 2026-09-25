from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .battery_flow import integrate_signed_power, is_power_unit, normalize_power_w
from .const import (
    CONF_BATTERY_POWER_1,
    CONF_BATTERY_POWER_2,
    CONF_BATTERY_POWER_3,
    CONF_SOC_1,
    CONF_SOC_2,
    CONF_SOC_3,
)
from .v021_coordinator import EnergyPlannerV021Coordinator

_BATTERY_FLOW_STORE_VERSION = 1
_MAX_INTEGRATION_GAP_SECONDS = 5 * 60
_SAVE_DELAY_SECONDS = 60


class EnergyPlannerV022Coordinator(EnergyPlannerV021Coordinator):
    """v0.1.22 exposes whole-bank battery power and cumulative energy flow."""

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry)
        self._battery_flow_store: Store[dict[str, Any]] = Store(
            hass,
            _BATTERY_FLOW_STORE_VERSION,
            f"energy_planner.{entry.entry_id}.battery_flow",
        )
        self._battery_flow_data: dict[str, Any] | None = None

    async def _ensure_battery_flow_data(self) -> dict[str, Any]:
        if self._battery_flow_data is None:
            loaded = await self._battery_flow_store.async_load()
            if not isinstance(loaded, dict):
                loaded = {}
            try:
                charged = max(float(loaded.get("charged_kwh", 0.0)), 0.0)
            except (TypeError, ValueError):
                charged = 0.0
            try:
                discharged = max(float(loaded.get("discharged_kwh", 0.0)), 0.0)
            except (TypeError, ValueError):
                discharged = 0.0
            self._battery_flow_data = {
                "charged_kwh": charged,
                "discharged_kwh": discharged,
                "last_power_w": None,
                "last_sample_at": None,
            }
        return self._battery_flow_data

    def _battery_flow_data_to_save(self) -> dict[str, float]:
        data = self._battery_flow_data or {}
        return {
            "charged_kwh": max(float(data.get("charged_kwh", 0.0)), 0.0),
            "discharged_kwh": max(float(data.get("discharged_kwh", 0.0)), 0.0),
        }

    @staticmethod
    def _derived_power_entity(soc_entity: str | None) -> str | None:
        if not soc_entity:
            return None
        if soc_entity.endswith("_battery"):
            return f"{soc_entity[:-len('_battery')]}_power"
        if soc_entity.endswith("_battery_level"):
            return f"{soc_entity[:-len('_battery_level')]}_power"
        return None

    def _resolved_power_entities(self) -> tuple[str, str, str] | None:
        cfg = self.cfg
        configured = (
            cfg.get(CONF_BATTERY_POWER_1),
            cfg.get(CONF_BATTERY_POWER_2),
            cfg.get(CONF_BATTERY_POWER_3),
        )
        socs = (
            cfg.get(CONF_SOC_1),
            cfg.get(CONF_SOC_2),
            cfg.get(CONF_SOC_3),
        )

        resolved: list[str] = []
        for power_entity, soc_entity in zip(configured, socs):
            candidates: list[str] = []
            if power_entity:
                candidates.append(str(power_entity))
            derived = self._derived_power_entity(soc_entity)
            if derived and derived not in candidates:
                candidates.append(derived)

            candidate = None
            for entity_id in candidates:
                state = self.hass.states.get(entity_id)
                if state is None:
                    continue
                if not is_power_unit(state.attributes.get("unit_of_measurement")):
                    continue
                candidate = entity_id
                break

            if candidate is None:
                return None
            resolved.append(candidate)
        return resolved[0], resolved[1], resolved[2]

    def _battery_bank_power_w(
        self,
    ) -> tuple[float | None, tuple[str, str, str] | None]:
        entities = self._resolved_power_entities()
        if entities is None:
            return None, None

        values: list[float] = []
        for entity_id in entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in {"unknown", "unavailable", "none", ""}:
                return None, entities
            try:
                value = float(state.state)
            except (TypeError, ValueError):
                return None, entities
            values.append(
                normalize_power_w(
                    value,
                    state.attributes.get("unit_of_measurement"),
                )
            )
        return sum(values), entities

    async def _battery_energy_outputs(self, *, now: datetime) -> dict[str, Any]:
        data = await self._ensure_battery_flow_data()
        power_w, entities = self._battery_bank_power_w()

        previous_power = data.get("last_power_w")
        previous_at = data.get("last_sample_at")
        incremented = False

        if (
            power_w is not None
            and previous_power is not None
            and isinstance(previous_at, datetime)
        ):
            elapsed = (now - previous_at).total_seconds()
            if 0.0 < elapsed <= _MAX_INTEGRATION_GAP_SECONDS:
                increment = integrate_signed_power(
                    float(previous_power),
                    float(power_w),
                    elapsed,
                )
                if increment.charged_kwh > 0.0 or increment.discharged_kwh > 0.0:
                    data["charged_kwh"] = (
                        float(data["charged_kwh"]) + increment.charged_kwh
                    )
                    data["discharged_kwh"] = (
                        float(data["discharged_kwh"]) + increment.discharged_kwh
                    )
                    incremented = True

        if power_w is None:
            data["last_power_w"] = None
            data["last_sample_at"] = None
        else:
            data["last_power_w"] = float(power_w)
            data["last_sample_at"] = now

        if incremented:
            self._battery_flow_store.async_delay_save(
                self._battery_flow_data_to_save,
                _SAVE_DELAY_SECONDS,
            )

        return {
            "battery_bank_power_w": power_w,
            "battery_energy_charged_kwh": float(data["charged_kwh"]),
            "battery_energy_discharged_kwh": float(data["discharged_kwh"]),
            "battery_power_source_entities": (
                ", ".join(entities) if entities is not None else "unavailable"
            ),
        }

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data.update(await self._battery_energy_outputs(now=dt_util.now()))
        return data
