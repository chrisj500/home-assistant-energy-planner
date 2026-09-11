from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_sunrise, async_track_time_change

from .const import PLATFORMS
from .coordinator import EnergyPlannerCoordinator


type EnergyPlannerConfigEntry = ConfigEntry[EnergyPlannerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EnergyPlannerConfigEntry) -> bool:
    coordinator = EnergyPlannerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def _run_policy(*_args) -> None:
        await coordinator.async_apply_headroom_policy()

    entry.async_on_unload(
        async_track_time_change(hass, _run_policy, hour=18, minute=30, second=0)
    )
    entry.async_on_unload(
        async_track_time_change(hass, _run_policy, hour=23, minute=0, second=0)
    )
    entry.async_on_unload(
        async_track_sunrise(hass, _run_policy, offset=timedelta(hours=-1))
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyPlannerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: EnergyPlannerConfigEntry) -> None:
    """Apply option changes without unloading/reloading the integration."""
    coordinator = entry.runtime_data
    await coordinator.async_request_refresh()
