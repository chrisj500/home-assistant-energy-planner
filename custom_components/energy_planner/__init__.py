from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .v015_coordinator import EnergyPlannerV015Coordinator


type EnergyPlannerConfigEntry = ConfigEntry[EnergyPlannerV015Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EnergyPlannerConfigEntry) -> bool:
    coordinator = EnergyPlannerV015Coordinator(hass, entry)
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
