from __future__ import annotations

import json
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .config_migration import (
    CONFIG_ENTRY_VERSION,
    remove_legacy_topology_keys,
)
from .solar_policy import migrated_solar_options
from .solar_learning_coordinator import EnergyPlannerSolarLearningCoordinator

_LOGGER = logging.getLogger(__name__)
SERVICE_RESTORE_SOLAR_LEARNING = "restore_solar_learning"
ATTR_RECOVERY_FILE = "recovery_file"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
RESTORE_SCHEMA = vol.Schema({
    vol.Required(ATTR_RECOVERY_FILE): cv.string,
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
})

type EnergyPlannerConfigEntry = ConfigEntry[EnergyPlannerSolarLearningCoordinator]


def _load_recovery_bundle(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-level actions before config entries are loaded."""
    async def async_restore_solar_learning(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if entry_id:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                raise ServiceValidationError("Energy Planner configuration entry not found")
            entries = [entry]
        else:
            entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)
                       if entry.state is ConfigEntryState.LOADED]
            if len(entries) != 1:
                raise ServiceValidationError(
                    "Specify config_entry_id when more than one Energy Planner entry exists or none is loaded"
                )
        entry = entries[0]
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError("Energy Planner configuration entry is not loaded")

        config_root = Path(hass.config.config_dir).resolve()
        path = Path(hass.config.path(call.data[ATTR_RECOVERY_FILE])).resolve()
        if path != config_root and config_root not in path.parents:
            raise ServiceValidationError("Recovery file must be inside the Home Assistant config directory")
        if not path.is_file():
            raise ServiceValidationError(f"Recovery file does not exist: {path}")
        if path.stat().st_size > 25_000_000:
            raise ServiceValidationError("Recovery file is unexpectedly large")
        try:
            bundle = await hass.async_add_executor_job(_load_recovery_bundle, path)
            audit = await entry.runtime_data.async_restore_solar_learning(bundle)
        except (OSError, json.JSONDecodeError, ValueError) as err:
            raise ServiceValidationError(f"Solar learning recovery failed: {err}") from err
        _LOGGER.warning("Solar learning recovery completed: %s", audit)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_SOLAR_LEARNING,
        async_restore_solar_learning,
        schema=RESTORE_SCHEMA,
    )
    return True


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: EnergyPlannerConfigEntry,
) -> bool:
    """Migrate legacy Energy Planner config entries before setup."""
    if entry.version > CONFIG_ENTRY_VERSION:
        _LOGGER.error(
            "Cannot migrate Energy Planner config entry from version %s to %s",
            entry.version,
            CONFIG_ENTRY_VERSION,
        )
        return False

    data, options, changed = remove_legacy_topology_keys(
        dict(entry.data),
        dict(entry.options),
    )
    if changed or entry.version != CONFIG_ENTRY_VERSION:
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=CONFIG_ENTRY_VERSION,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EnergyPlannerConfigEntry) -> bool:
    # Replace the known legacy YAML correction with the provider's raw fallback.
    # Interval forecasts and live correction are owned by Energy Planner.
    options = migrated_solar_options(entry.data, entry.options)
    if options is not None:
        hass.config_entries.async_update_entry(entry, options=options)
    coordinator = EnergyPlannerSolarLearningCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyPlannerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: EnergyPlannerConfigEntry) -> None:
    """Apply option changes without unloading/reloading the integration."""
    coordinator = entry.runtime_data
    await coordinator.async_request_refresh()
