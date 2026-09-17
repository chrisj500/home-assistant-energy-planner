from __future__ import annotations

from copy import deepcopy

from homeassistant.util import dt as dt_util

from .const import CONF_ACTUAL_SOLAR_POWER, CONF_SOLAR_REMAINING
from .coordinator import _num, _solar_window
from .forecast_solar_shadow import interval_points_from_payload
from .headroom import correct_current_day_points
from .v018_coordinator import EnergyPlannerV018Coordinator


class EnergyPlannerV021Coordinator(EnergyPlannerV018Coordinator):
    """v0.1.21 anchors today's rolling solar curve to live Enphase production.

    The current-day Forecast.Solar interval curve is still energy-calibrated to the
    configured remaining-solar total. During daylight, the instantaneous curve is
    then anchored to live solar output and blended back toward the provider shape
    while preserving the corrected remaining-energy total. Future days remain raw.
    """

    def _rolling_ev_outputs(self, baseline: dict) -> dict:
        payload = self._estimate_payload
        if not isinstance(payload, dict):
            return super()._rolling_ev_outputs(baseline)

        now = dt_util.now()
        sunrise, sunset = _solar_window(self.hass, now.date())
        points = interval_points_from_payload(payload, now, assume_utc=True)
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunrise=sunrise,
            sunset=sunset,
            corrected_remaining_kwh=_num(self.hass, self.cfg.get(CONF_SOLAR_REMAINING)),
            actual_solar_w=_num(self.hass, self.cfg.get(CONF_ACTUAL_SOLAR_POWER)),
        )

        corrected_payload = deepcopy(payload)
        result = corrected_payload.get("result")
        if isinstance(result, dict) and correction.points:
            result["watts"] = {
                point.at.isoformat(): max(float(point.watts), 0.0)
                for point in correction.points
            }

        original_payload = self._estimate_payload
        self._estimate_payload = corrected_payload
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
                "rolling_current_day_live_anchor_w": correction.live_anchor_w,
                "rolling_current_day_live_blend_minutes": correction.live_blend_minutes,
                "rolling_current_day_compensated_remaining_kwh": correction.compensated_remaining_kwh,
            }
        )
        return output

    async def _async_update_data(self) -> dict:
        data = await super()._async_update_data()

        rows = data.get("rolling_day_plans")
        if not isinstance(rows, list) or not rows:
            return data

        today = dt_util.now().date().isoformat()
        row = next(
            (
                candidate
                for candidate in rows
                if isinstance(candidate, dict) and str(candidate.get("date")) == today
            ),
            None,
        )
        if row is None:
            return data

        source = str(
            data.get("rolling_current_day_forecast_source")
            or row.get("forecast_source")
            or "live_anchored_current_day_interval_curve"
        )
        if source != "live_anchored_current_day_interval_curve":
            return data

        def value(key: str):
            raw = row.get(key)
            try:
                return None if raw is None else float(raw)
            except (TypeError, ValueError):
                return None

        sunset_soc = value("sunset_soc_pct")
        battery_gain = value("battery_gain_kwh")
        grid_import = value("grid_import_kwh")
        export = value("export_kwh")
        capacity_export = value("capacity_export_kwh")
        power_export = value("power_export_kwh")
        solar_after_load = value("solar_after_house_load_kwh")

        if sunset_soc is None:
            return data

        previous_live = data.get("projected_sunset_soc")
        try:
            previous_live_soc = float(previous_live) if previous_live is not None else None
        except (TypeError, ValueError):
            previous_live_soc = None

        data.update(
            {
                # Make the same live-anchored day-0 curve authoritative everywhere
                # the dashboard exposes today's sunset outlook.
                "projected_sunset_soc": sunset_soc,
                "projected_charge_to_sunset": battery_gain,
                "projection_available_ac": solar_after_load,
                "projection_model": "live_anchored_interval+physical_surplus_v1",
                "today_projected_sunset_soc": sunset_soc,
                "today_projected_max_soc": sunset_soc,
                "today_predicted_export": export,
                "today_predicted_grid_import": grid_import,
                "today_capacity_limited_export": capacity_export,
                "today_power_limited_export": power_export,
                "forecast_solar_shadow_projected_sunset_soc": sunset_soc,
                "forecast_solar_shadow_sunset_soc_delta": (
                    sunset_soc - previous_live_soc
                    if previous_live_soc is not None
                    else None
                ),
                "forecast_solar_shadow_charge_to_sunset": battery_gain,
                "forecast_solar_shadow_grid_import_today": grid_import,
                "forecast_solar_shadow_export_today": export,
                "forecast_solar_shadow_model": "live_anchored_current_day_interval_curve",
                "next_sunset_date": today,
                "next_sunset_soc": sunset_soc,
                "next_sunset_expected_charge": battery_gain,
                "next_sunset_expected_grid_import": grid_import,
                "next_sunset_expected_export": export,
                "next_sunset_forecast_source": "live_anchored_current_day_interval_curve",
            }
        )
        return data
