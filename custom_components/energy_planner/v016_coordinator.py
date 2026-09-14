from __future__ import annotations

from homeassistant.const import UnitOfTemperature
from homeassistant.util import dt as dt_util

from .const import CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH
from .coordinator import _solar_window
from .dashboard_model import (
    normalize_forecast_temperature_c,
    select_next_sunset_forecast,
)
from .v015_coordinator import EnergyPlannerV015Coordinator


class EnergyPlannerV016Coordinator(EnergyPlannerV015Coordinator):
    """v0.1.16 stable dashboard semantics and weather-unit normalization."""

    async def _async_update_data(self) -> dict:
        data = await super()._async_update_data()
        now = dt_util.now()
        sunrise, sunset = _solar_window(self.hass, now.date())

        if sunrise is not None and now < sunrise:
            phase = "before_sunrise"
        elif sunset is not None and now >= sunset:
            phase = "after_sunset"
        else:
            phase = "daylight"

        capacity = float(self.cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        next_sunset = select_next_sunset_forecast(
            phase=phase,
            data=data,
            capacity_kwh=capacity,
        )

        raw_weather_temperature = data.get("forecast_solar_weather_temperature_now")
        try:
            raw_temperature = (
                None if raw_weather_temperature is None else float(raw_weather_temperature)
            )
        except (TypeError, ValueError):
            raw_temperature = None
        ha_temperature_unit = self.hass.config.units.temperature_unit
        normalized_c, temperature_source = normalize_forecast_temperature_c(
            raw_temperature,
            assumed_input_unit=str(ha_temperature_unit),
        )

        data.update(
            {
                "next_sunset_date": next_sunset.date,
                "next_sunset_soc": next_sunset.soc_pct,
                "next_sunset_expected_charge": next_sunset.expected_charge_kwh,
                "next_sunset_expected_grid_import": next_sunset.expected_grid_import_kwh,
                "next_sunset_expected_export": next_sunset.expected_export_kwh,
                "next_sunset_forecast_source": next_sunset.source,
                "forecast_solar_weather_temperature_raw": raw_temperature,
                "forecast_solar_weather_temperature_now": normalized_c,
                "forecast_solar_weather_temperature_source": temperature_source,
                "forecast_solar_weather_input_unit": str(ha_temperature_unit),
            }
        )
        return data
