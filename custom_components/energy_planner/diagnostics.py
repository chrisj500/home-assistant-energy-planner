"""Diagnostics support for Energy Planner."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State

from .const import CONF_FORECAST_SOLAR_API_KEY

TO_REDACT = {CONF_FORECAST_SOLAR_API_KEY}
HVAC_ENTITY_KEYS = {
    "hvac_thermostat", "hvac_condenser", "hvac_blower",
    "hvac_outdoor_temperature", "hvac_weather", "hvac_stale",
}


def _state_payload(state: State | None) -> dict[str, Any]:
    if state is None:
        return {
            "exists": False,
            "state": None,
            "attributes": {},
            "last_changed": None,
            "last_updated": None,
            "last_reported": None,
        }
    return {
        "exists": True,
        "state": state.state,
        "attributes": dict(state.attributes),
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
        "last_reported": getattr(state, "last_reported", state.last_updated).isoformat(),
    }


def _configured_entity_ids(entry: ConfigEntry) -> dict[str, str]:
    merged = {**entry.data, **entry.options}
    entities: dict[str, str] = {}
    for key, value in merged.items():
        if not isinstance(value, str) or "." not in value:
            continue
        if (
            key.startswith("soc_entity_")
            or key.endswith("_entity")
            or key.startswith("battery_power_entity_")
            or key in HVAC_ENTITY_KEYS
        ):
            entities[key] = value
    return entities


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return a single support bundle for planner configuration and live state."""
    coordinator = entry.runtime_data
    configured_entities = _configured_entity_ids(entry)

    planner_entities: dict[str, Any] = {}
    for state in hass.states.async_all():
        if state.entity_id.startswith(
            ("sensor.energy_planner_", "binary_sensor.energy_planner_")
        ):
            planner_entities[state.entity_id] = _state_payload(state)

    internal_status = {
        "forecast_interval_origin": getattr(coordinator, "_estimate_origin", None),
        "forecast_interval_last_success": (
            getattr(coordinator, "_estimate_last_success", None).isoformat()
            if getattr(coordinator, "_estimate_last_success", None) is not None
            else None
        ),
        "forecast_interval_last_attempt": (
            getattr(coordinator, "_estimate_last_attempt", None).isoformat()
            if getattr(coordinator, "_estimate_last_attempt", None) is not None
            else None
        ),
        "forecast_interval_error": getattr(coordinator, "_estimate_error", None),
        "hvac_persistence": deepcopy(
            getattr(coordinator, "_hvac_persistence", {})
        ),
        "hvac_memory_counts": {
            "samples": len(
                (getattr(coordinator, "_hvac_memory", None) or {}).get("samples", [])
            ),
            "recovery_cycles": len(
                (getattr(coordinator, "_hvac_memory", None) or {}).get(
                    "recovery_cycles", []
                )
            ),
            "thermal_samples": len(
                (getattr(coordinator, "_hvac_memory", None) or {}).get(
                    "thermal_samples", []
                )
            ),
        },
    }

    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "configured_entities": {
            key: {
                "entity_id": entity_id,
                **_state_payload(hass.states.get(entity_id)),
            }
            for key, entity_id in configured_entities.items()
        },
        "planner_entities": planner_entities,
        "coordinator_data": async_redact_data(
            deepcopy(getattr(coordinator, "data", {}) or {}),
            TO_REDACT,
        ),
        "internal_status": internal_status,
        "solar_learning_history": deepcopy({
            key: (getattr(coordinator, "_solar_memory", None) or {}).get(key)
            for key in ("identity", "last_issue", "reset_reason", "actual_hours", "pending", "scored")
        }),
    }
