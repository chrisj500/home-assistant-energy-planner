from __future__ import annotations

from homeassistant.util import dt as dt_util

from .const import CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH
from .coordinator import _solar_window
from .dashboard_model import select_next_sunset_forecast
from .v015_coordinator import EnergyPlannerV015Coordinator


class EnergyPlannerV016Coordinator(EnergyPlannerV015Coordinator):
    """v0.1.16 stable next-sunset semantics for dashboards and automations."""

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

        capacity = float(
            data.get(
                "battery_capacity_kwh",
                self.cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH),
            )
        )
        next_sunset = select_next_sunset_forecast(
            phase=phase,
            data=data,
            capacity_kwh=capacity,
        )

        data.update(
            {
                "next_sunset_date": next_sunset.date,
                "next_sunset_soc": next_sunset.soc_pct,
                "next_sunset_expected_charge": next_sunset.expected_charge_kwh,
                "next_sunset_expected_grid_import": next_sunset.expected_grid_import_kwh,
                "next_sunset_expected_export": next_sunset.expected_export_kwh,
                "next_sunset_forecast_source": next_sunset.source,
            }
        )
        return data
