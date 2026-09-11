from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector

from .const import (
    CONF_BACKUP_RESERVE,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_LIMIT,
    CONF_EV_HOME,
    CONF_EV_SOC,
    CONF_SOC_1,
    CONF_SOC_2,
    CONF_SOC_3,
    CONF_SOC_WEIGHTS,
    CONF_SOLAR_TODAY,
    CONF_SOLAR_TOMORROW,
    CONF_STORM_WARNING,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_MIN_RESERVE,
    DEFAULT_STRONG_SOLAR_KWH,
    DEFAULT_WEIGHTS,
    DOMAIN,
    OPT_AUTO_HEADROOM,
    OPT_EV_TARGET_SOC,
    OPT_MIN_RESERVE,
    OPT_STRONG_SOLAR_KWH,
)

SENSOR_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
NUMBER_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))
BINARY_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))
TRACKER_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="device_tracker"))


class EnergyPlannerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id("energy_planner")
            self._abort_if_unique_id_configured()
            title = user_input.pop(CONF_NAME, "Energy Planner")
            return self.async_create_entry(title=title, data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default="Energy Planner"): str,
                vol.Required(CONF_SOC_1): SENSOR_SELECTOR,
                vol.Required(CONF_SOC_2): SENSOR_SELECTOR,
                vol.Required(CONF_SOC_3): SENSOR_SELECTOR,
                vol.Optional(CONF_SOC_WEIGHTS, default=DEFAULT_WEIGHTS): str,
                vol.Optional(CONF_CAPACITY_KWH, default=DEFAULT_CAPACITY_KWH): vol.Coerce(float),
                vol.Required(CONF_CHARGE_LIMIT): NUMBER_SELECTOR,
                vol.Required(CONF_BACKUP_RESERVE): NUMBER_SELECTOR,
                vol.Required(CONF_STORM_WARNING): BINARY_SELECTOR,
                vol.Required(CONF_SOLAR_TODAY): SENSOR_SELECTOR,
                vol.Required(CONF_SOLAR_TOMORROW): SENSOR_SELECTOR,
                vol.Optional(CONF_EV_SOC): SENSOR_SELECTOR,
                vol.Optional(CONF_EV_HOME): TRACKER_SELECTOR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry):
        return EnergyPlannerOptionsFlow(config_entry)


class EnergyPlannerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        current = {**self._entry.data, **self._entry.options}
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    OPT_AUTO_HEADROOM,
                    default=bool(current.get(OPT_AUTO_HEADROOM, False)),
                ): bool,
                vol.Optional(
                    OPT_MIN_RESERVE,
                    default=float(current.get(OPT_MIN_RESERVE, DEFAULT_MIN_RESERVE)),
                ): vol.All(vol.Coerce(float), vol.Range(min=5, max=50)),
                vol.Optional(
                    OPT_STRONG_SOLAR_KWH,
                    default=float(current.get(OPT_STRONG_SOLAR_KWH, DEFAULT_STRONG_SOLAR_KWH)),
                ): vol.All(vol.Coerce(float), vol.Range(min=5, max=100)),
                vol.Optional(
                    OPT_EV_TARGET_SOC,
                    default=float(current.get(OPT_EV_TARGET_SOC, DEFAULT_EV_TARGET_SOC)),
                ): vol.All(vol.Coerce(float), vol.Range(min=50, max=100)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
