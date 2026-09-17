from __future__ import annotations

from copy import deepcopy

from homeassistant.util import dt as dt_util

from .const import CONF_SOLAR_REMAINING
from .coordinator import _num, _solar_window
from .forecast_solar_shadow import coerce_datetime, interval_points_from_payload
from .headroom import correct_current_day_points
from .v016_coordinator import EnergyPlannerV016Coordinator


class EnergyPlannerV017Coordinator(EnergyPlannerV016Coordinator):
    """v0.1.17 makes the corrected rolling model authoritative for headroom risk.

    Forecast.Solar still provides the interval shape. For the current solar day,
    that shape is scaled to the configured locally corrected remaining-energy
    forecast before rolling battery, export, and EV decisions are calculated.
    Future days remain on the provider forecast so today's production bias does
    not leak into tomorrow's weather assumptions.
    """

    def _rolling_ev_outputs(self, baseline: dict) -> dict:
        payload = self._estimate_payload
        if not isinstance(payload, dict):
            return super()._rolling_ev_outputs(baseline)

        now = dt_util.now()
        sunrise, sunset = _solar_window(self.hass, now.date())
        points = interval_points_from_payload(payload, now, assume_utc=True)
        corrected_remaining = _num(self.hass, self.cfg.get(CONF_SOLAR_REMAINING))
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunrise=sunrise,
            sunset=sunset,
            corrected_remaining_kwh=corrected_remaining,
        )

        scaled_payload = payload
        if correction.source == "locally_corrected_current_day_interval_curve":
            scaled_payload = deepcopy(payload)
            result = scaled_payload.get("result")
            watts = result.get("watts") if isinstance(result, dict) else None
            if isinstance(watts, dict):
                local_tz = now.tzinfo
                today = now.date()
                for raw_time, raw_watts in list(watts.items()):
                    at = coerce_datetime(raw_time, now, assume_utc=True)
                    if at is None or at.astimezone(local_tz).date() != today:
                        continue
                    try:
                        watts[raw_time] = max(float(raw_watts), 0.0) * correction.scale_factor
                    except (TypeError, ValueError):
                        continue

        original_payload = self._estimate_payload
        self._estimate_payload = scaled_payload
        try:
            output = super()._rolling_ev_outputs(baseline)
        finally:
            self._estimate_payload = original_payload

        output.update(
            {
                "rolling_current_day_scale_factor": correction.scale_factor,
                "rolling_current_day_forecast_source": correction.source,
                "rolling_current_day_raw_remaining_kwh": correction.raw_remaining_kwh,
                "rolling_current_day_corrected_remaining_kwh": correction.corrected_remaining_kwh,
            }
        )
        return output

    async def _async_update_data(self) -> dict:
        data = await super()._async_update_data()
        today = dt_util.now().date().isoformat()
        risk_date = str(data.get("rolling_headroom_risk_date") or "none")
        try:
            shortfall = max(float(data.get("rolling_headroom_shortfall_kwh") or 0.0), 0.0)
        except (TypeError, ValueError):
            shortfall = 0.0
        try:
            capacity_export = max(float(data.get("rolling_risk_export_kwh") or 0.0), 0.0)
        except (TypeError, ValueError):
            capacity_export = 0.0

        risk_today = risk_date == today and max(shortfall, capacity_export) >= 0.25
        if risk_today:
            status = "risk_today"
            action = "Use useful flexible load before storage fills; preserve backup reserve."
        elif risk_date not in {"", "none", "None"}:
            status = "risk_future"
            action = "Preserve flexible-load capacity for the forecast headroom-risk day."
        else:
            status = "clear"
            action = "No material stationary-battery headroom action is currently required."

        data.update(
            {
                "authoritative_headroom_risk": risk_today,
                "authoritative_headroom_status": status,
                "authoritative_headroom_risk_date": risk_date,
                "authoritative_headroom_shortfall_kwh": shortfall,
                "authoritative_headroom_capacity_export_kwh": capacity_export,
                "authoritative_headroom_action": action,
            }
        )
        return data
