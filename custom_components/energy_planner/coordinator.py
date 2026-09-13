from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTUAL_SOLAR_POWER,
    CONF_BACKUP_RESERVE,
    CONF_BASE_LOAD_POWER,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_LIMIT,
    CONF_EV_HOME,
    CONF_EV_SOC,
    CONF_EXPECTED_LOAD_REMAINING,
    CONF_SOC_1,
    CONF_SOC_2,
    CONF_SOC_3,
    CONF_SOC_WEIGHTS,
    CONF_SOLAR_PEAK_TIME,
    CONF_SOLAR_PEAK_TIME_TOMORROW,
    CONF_SOLAR_REMAINING,
    CONF_SOLAR_TODAY,
    CONF_SOLAR_TOMORROW,
    CONF_STORM_WARNING,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_EFFICIENCY,
    DEFAULT_DISCRETIONARY_THRESHOLD_KWH,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_HARVEST_CAPTURE_FACTOR,
    DEFAULT_MIN_RESERVE,
    DEFAULT_PREFERRED_IMPORT_W,
    DEFAULT_STRONG_SOLAR_KWH,
    DEFAULT_WEIGHTS,
    OPT_AUTO_HEADROOM,
    OPT_CHARGE_EFFICIENCY,
    OPT_DISCRETIONARY_THRESHOLD_KWH,
    OPT_EV_TARGET_SOC,
    OPT_HARVEST_CAPTURE_FACTOR,
    OPT_MIN_RESERVE,
    OPT_PREFERRED_IMPORT_W,
    OPT_STRONG_SOLAR_KWH,
)
from .projection import project_sunset_soc
from .strategy import (
    STRATEGY_CREATE_HEADROOM,
    STRATEGY_HOLD,
    STRATEGY_INSUFFICIENT_DATA,
    STRATEGY_PRESERVE_FOR_RESILIENCE,
    STRATEGY_USE_DISCRETIONARY_LOADS,
    plan_solar_period,
)

_LOGGER = logging.getLogger(__name__)


def _num(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unknown", "unavailable", "none", ""}:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _is_on(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unknown", "unavailable"}:
        return None
    return state.state == "on"


def _timestamp(hass: HomeAssistant, entity_id: str | None):
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unknown", "unavailable", "none", ""}:
        return None
    return dt_util.parse_datetime(state.state)


def _solar_window(hass: HomeAssistant, target_date: date):
    sunrise = get_astral_event_date(hass, SUN_EVENT_SUNRISE, target_date)
    sunset = get_astral_event_date(hass, SUN_EVENT_SUNSET, target_date)
    return sunrise, sunset


def _next_sunset(hass: HomeAssistant):
    sun = hass.states.get("sun.sun")
    if sun is None or sun.state != "above_horizon":
        return None
    setting = sun.attributes.get("next_setting")
    if not setting:
        return None
    if hasattr(setting, "tzinfo"):
        return setting
    return dt_util.parse_datetime(str(setting))


class EnergyPlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="Energy Planner",
            update_interval=timedelta(minutes=1),
        )
        self.entry = entry

    @property
    def cfg(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    async def _async_update_data(self) -> dict[str, Any]:
        cfg = self.cfg
        now = dt_util.now()
        today_date = now.date()
        next_day_date = today_date + timedelta(days=1)
        today_sunrise, today_sunset = _solar_window(self.hass, today_date)
        next_day_sunrise, next_day_sunset = _solar_window(self.hass, next_day_date)

        weights_raw = str(cfg.get(CONF_SOC_WEIGHTS, DEFAULT_WEIGHTS))
        try:
            weights = [float(x.strip()) for x in weights_raw.split(",")]
        except ValueError:
            weights = [3.0, 2.0, 3.0]
        if len(weights) != 3 or sum(weights) <= 0:
            weights = [3.0, 2.0, 3.0]

        socs = [
            _num(self.hass, cfg.get(CONF_SOC_1)),
            _num(self.hass, cfg.get(CONF_SOC_2)),
            _num(self.hass, cfg.get(CONF_SOC_3)),
        ]
        weighted_soc = None
        if all(value is not None for value in socs):
            weighted_soc = sum(value * weight for value, weight in zip(socs, weights)) / sum(weights)

        capacity = float(cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        stored = None if weighted_soc is None else capacity * weighted_soc / 100.0
        charge_limit = _num(self.hass, cfg.get(CONF_CHARGE_LIMIT))
        headroom = None
        if stored is not None and charge_limit is not None:
            headroom = max(capacity * charge_limit / 100.0 - stored, 0.0)

        solar_today = _num(self.hass, cfg.get(CONF_SOLAR_TODAY))
        solar_tomorrow = _num(self.hass, cfg.get(CONF_SOLAR_TOMORROW))
        upcoming = solar_today if now.hour < 12 else solar_tomorrow

        reserve = _num(self.hass, cfg.get(CONF_BACKUP_RESERVE))
        storm = _is_on(self.hass, cfg.get(CONF_STORM_WARNING))
        minimum_reserve = float(cfg.get(OPT_MIN_RESERVE, DEFAULT_MIN_RESERVE))
        strong_solar = float(cfg.get(OPT_STRONG_SOLAR_KWH, DEFAULT_STRONG_SOLAR_KWH))
        auto_headroom = bool(cfg.get(OPT_AUTO_HEADROOM, False))
        harvest_capture_factor = float(
            cfg.get(OPT_HARVEST_CAPTURE_FACTOR, DEFAULT_HARVEST_CAPTURE_FACTOR)
        )
        charge_efficiency = float(
            cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)
        )
        discretionary_threshold = float(
            cfg.get(
                OPT_DISCRETIONARY_THRESHOLD_KWH,
                DEFAULT_DISCRETIONARY_THRESHOLD_KWH,
            )
        )

        ready = reserve is not None and storm is not None and upcoming is not None

        remaining_solar = _num(self.hass, cfg.get(CONF_SOLAR_REMAINING))
        expected_load = _num(self.hass, cfg.get(CONF_EXPECTED_LOAD_REMAINING))
        actual_solar_w = _num(self.hass, cfg.get(CONF_ACTUAL_SOLAR_POWER))
        peak_time = _timestamp(self.hass, cfg.get(CONF_SOLAR_PEAK_TIME))
        peak_time_tomorrow = _timestamp(
            self.hass, cfg.get(CONF_SOLAR_PEAK_TIME_TOMORROW)
        )
        base_load_power_w = _num(self.hass, cfg.get(CONF_BASE_LOAD_POWER))

        ev_soc = _num(self.hass, cfg.get(CONF_EV_SOC))
        ev_home_entity = cfg.get(CONF_EV_HOME)
        ev_home = None
        if ev_home_entity:
            state = self.hass.states.get(ev_home_entity)
            ev_home = None if state is None else state.state == "home"
        ev_target = float(cfg.get(OPT_EV_TARGET_SOC, DEFAULT_EV_TARGET_SOC))
        ev_needs_charge = ev_soc is not None and ev_soc < ev_target - 1
        ev_available = ev_home is not False and ev_needs_charge

        # Existing intraday sunset projection remains the live current-day
        # forecast once the sun is up.
        projected_sunset_soc = None
        projected_charge_to_sunset = None
        projection_available_ac = None
        projection_model = None
        sunset = _next_sunset(self.hass)
        projection_inputs_ready = all(
            value is not None
            for value in (
                weighted_soc,
                charge_limit,
                remaining_solar,
                expected_load,
                actual_solar_w,
                peak_time,
                sunset,
            )
        )
        projection = None
        if projection_inputs_ready:
            projection = project_sunset_soc(
                now=now,
                sunset=sunset,
                current_soc_pct=weighted_soc,
                capacity_kwh=capacity,
                charge_limit_pct=charge_limit,
                remaining_solar_kwh=remaining_solar,
                expected_load_remaining_kwh=expected_load,
                current_solar_w=actual_solar_w,
                peak_time=peak_time,
                preferred_import_w=float(
                    cfg.get(OPT_PREFERRED_IMPORT_W, DEFAULT_PREFERRED_IMPORT_W)
                ),
                harvest_capture_factor=harvest_capture_factor,
                charge_efficiency=charge_efficiency,
            )
            projected_sunset_soc = projection.projected_soc
            projected_charge_to_sunset = projection.projected_charge_kwh
            projection_available_ac = projection.projected_available_ac_kwh
            projection_model = projection.model
        elif weighted_soc is not None and sunset is None:
            projected_sunset_soc = weighted_soc
            projected_charge_to_sunset = 0.0
            projection_available_ac = 0.0
            projection_model = "nighttime_hold"

        # TODAY PLAN -------------------------------------------------------
        today_plan_ready = False
        today_strategy = STRATEGY_INSUFFICIENT_DATA
        today_strategy_reason = "Today's planning inputs are incomplete."
        today_projected_max_soc = None
        today_projected_sunset_soc = None
        today_predicted_export = None
        today_predicted_grid_import = None
        today_discretionary_energy = None
        today_recommended_presolar_discharge = None
        today_projection_model = None

        before_sunrise = today_sunrise is not None and now < today_sunrise
        after_sunset = today_sunset is not None and now >= today_sunset
        daylight = (
            today_sunrise is not None
            and today_sunset is not None
            and today_sunrise <= now < today_sunset
        )

        if before_sunrise:
            today_required_values = {
                "battery SOC": weighted_soc,
                "charge limit": charge_limit,
                "today solar forecast": solar_today,
                "base load power": base_load_power_w,
                "storm state": storm,
                "today sunrise": today_sunrise,
                "today sunset": today_sunset,
            }
            missing_today = [
                label for label, value in today_required_values.items() if value is None
            ]
            if not missing_today:
                today_plan = plan_solar_period(
                    sunrise=today_sunrise,
                    sunset=today_sunset,
                    current_soc_pct=weighted_soc,
                    capacity_kwh=capacity,
                    charge_limit_pct=charge_limit,
                    minimum_reserve_pct=minimum_reserve,
                    solar_forecast_kwh=solar_today,
                    base_load_power_w=base_load_power_w,
                    peak_time=peak_time,
                    harvest_capture_factor=harvest_capture_factor,
                    charge_efficiency=charge_efficiency,
                    storm_active=storm,
                    ev_soc_pct=ev_soc,
                    ev_target_soc_pct=ev_target,
                    ev_home=ev_home,
                    discretionary_threshold_kwh=discretionary_threshold,
                    allow_presolar_discharge=True,
                )
                today_plan_ready = True
                today_strategy = today_plan.strategy
                today_strategy_reason = today_plan.reason
                today_projected_max_soc = today_plan.projected_max_soc_pct
                today_projected_sunset_soc = today_plan.projected_sunset_soc_pct
                today_predicted_export = today_plan.predicted_export_kwh
                today_predicted_grid_import = today_plan.predicted_grid_import_kwh
                today_discretionary_energy = today_plan.discretionary_energy_kwh
                today_recommended_presolar_discharge = (
                    today_plan.recommended_overnight_discharge_kwh
                )
                today_projection_model = today_plan.model
            else:
                today_strategy_reason = "Missing: " + ", ".join(missing_today) + "."
        elif daylight and projection is not None:
            today_plan_ready = True
            accepted_ac_kwh = (
                projection.projected_charge_kwh / charge_efficiency
                if charge_efficiency > 0
                else 0.0
            )
            predicted_export = max(
                projection.projected_available_ac_kwh - accepted_ac_kwh,
                0.0,
            )
            today_predicted_export = predicted_export
            today_discretionary_energy = (
                predicted_export
                if predicted_export >= discretionary_threshold
                else 0.0
            )
            today_projected_max_soc = projection.projected_soc
            today_projected_sunset_soc = projection.projected_soc
            today_recommended_presolar_discharge = 0.0
            today_projection_model = f"live_{projection.model}"
            if storm:
                today_strategy = STRATEGY_PRESERVE_FOR_RESILIENCE
                today_strategy_reason = (
                    "Storm protection is active; preserve stored energy today."
                )
            elif predicted_export >= discretionary_threshold and ev_available:
                today_strategy = STRATEGY_USE_DISCRETIONARY_LOADS
                today_strategy_reason = (
                    f"About {predicted_export:.2f} kWh may otherwise export "
                    "during the remaining solar window; use the EV or another "
                    "flexible load if practical."
                )
            elif predicted_export >= discretionary_threshold:
                today_strategy = STRATEGY_USE_DISCRETIONARY_LOADS
                today_strategy_reason = (
                    f"About {predicted_export:.2f} kWh may otherwise export "
                    "during the remaining solar window; use a flexible load if "
                    "practical. Do not discharge the stationary battery during "
                    "daylight."
                )
            else:
                today_strategy = STRATEGY_HOLD
                today_strategy_reason = (
                    "Preserve stored solar; no meaningful remaining export risk "
                    "is modeled today."
                )
        elif after_sunset and weighted_soc is not None:
            today_plan_ready = True
            today_strategy = STRATEGY_HOLD
            today_strategy_reason = (
                "Today's solar window is complete; preserve stored solar overnight."
            )
            today_projected_max_soc = weighted_soc
            today_projected_sunset_soc = weighted_soc
            today_predicted_export = 0.0
            today_discretionary_energy = 0.0
            today_recommended_presolar_discharge = 0.0
            today_projection_model = "day_complete"

        # NEXT-DAY PLAN ----------------------------------------------------
        # Crucially, start the next day from TODAY'S projected sunset SOC,
        # not the battery SOC measured right now.
        projected_next_day_start_soc = today_projected_sunset_soc
        if projected_next_day_start_soc is None and after_sunset:
            projected_next_day_start_soc = weighted_soc

        next_day_plan_ready = False
        next_day_strategy = STRATEGY_INSUFFICIENT_DATA
        next_day_strategy_reason = "Next-day planning inputs are incomplete."
        required_headroom_next_day = None
        headroom_margin_next_day = None
        headroom_shortfall_next_day = None
        recommended_discharge_before_next_day = None
        projected_max_soc_next_day = None
        projected_sunset_soc_next_day = None
        predicted_export_next_day = None
        predicted_grid_import_next_day = None
        discretionary_energy_next_day = None
        next_day_projection_model = None
        planned_next_day_start_soc = None

        next_day_required_values = {
            "projected start SOC": projected_next_day_start_soc,
            "charge limit": charge_limit,
            "next-day solar forecast": solar_tomorrow,
            "base load power": base_load_power_w,
            "storm state": storm,
            "next-day sunrise": next_day_sunrise,
            "next-day sunset": next_day_sunset,
        }
        missing_next_day = [
            label for label, value in next_day_required_values.items() if value is None
        ]
        if not missing_next_day:
            next_day_plan = plan_solar_period(
                sunrise=next_day_sunrise,
                sunset=next_day_sunset,
                current_soc_pct=projected_next_day_start_soc,
                capacity_kwh=capacity,
                charge_limit_pct=charge_limit,
                minimum_reserve_pct=minimum_reserve,
                solar_forecast_kwh=solar_tomorrow,
                base_load_power_w=base_load_power_w,
                peak_time=peak_time_tomorrow,
                harvest_capture_factor=harvest_capture_factor,
                charge_efficiency=charge_efficiency,
                storm_active=storm,
                ev_soc_pct=ev_soc,
                ev_target_soc_pct=ev_target,
                ev_home=ev_home,
                discretionary_threshold_kwh=discretionary_threshold,
                allow_presolar_discharge=True,
            )
            next_day_plan_ready = True
            next_day_strategy = next_day_plan.strategy
            next_day_strategy_reason = next_day_plan.reason
            required_headroom_next_day = next_day_plan.required_headroom_kwh
            headroom_margin_next_day = next_day_plan.headroom_margin_kwh
            headroom_shortfall_next_day = next_day_plan.headroom_shortfall_kwh
            recommended_discharge_before_next_day = (
                next_day_plan.recommended_overnight_discharge_kwh
            )
            planned_next_day_start_soc = next_day_plan.planned_start_soc_pct
            projected_max_soc_next_day = next_day_plan.projected_max_soc_pct
            projected_sunset_soc_next_day = next_day_plan.projected_sunset_soc_pct
            predicted_export_next_day = next_day_plan.predicted_export_kwh
            predicted_grid_import_next_day = next_day_plan.predicted_grid_import_kwh
            discretionary_energy_next_day = next_day_plan.discretionary_energy_kwh
            next_day_projection_model = next_day_plan.model
        else:
            next_day_strategy_reason = (
                "Missing: " + ", ".join(missing_next_day) + "."
            )

        # Advisory-only: expose whether future headroom creation would be
        # recommended, but never actuate reserve or EcoFlow modes in v0.1.7.
        release = bool(
            auto_headroom
            and next_day_plan_ready
            and next_day_strategy == STRATEGY_CREATE_HEADROOM
            and not storm
        )

        if ev_soc is None:
            ev_plan = "EV SOC unavailable"
        elif ev_home is False:
            ev_plan = "EV away"
        elif ev_soc >= ev_target - 1:
            ev_plan = "No charge needed"
        elif today_strategy == STRATEGY_USE_DISCRETIONARY_LOADS:
            ev_plan = "Charge during today's solar window"
        elif next_day_strategy == STRATEGY_USE_DISCRETIONARY_LOADS:
            ev_plan = "Charge during next-day solar window"
        elif solar_tomorrow is not None and solar_tomorrow >= strong_solar:
            ev_plan = "Defer grid charging pending solar plan"
        else:
            ev_plan = "Grid charge when convenient"

        return {
            "weighted_soc": weighted_soc,
            "stored_energy": stored,
            "battery_headroom": headroom,
            "upcoming_solar": upcoming,
            "reserve": reserve,
            "storm": storm,
            "control_ready": ready,
            "headroom_release": release,
            "projected_sunset_soc": projected_sunset_soc,
            "projected_charge_to_sunset": projected_charge_to_sunset,
            "projection_available_ac": projection_available_ac,
            "projection_model": projection_model,
            "ev_soc": ev_soc,
            "ev_plan": ev_plan,
            "today_plan_date": today_date.isoformat(),
            "today_plan_ready": today_plan_ready,
            "today_strategy": today_strategy,
            "today_strategy_reason": today_strategy_reason,
            "today_projected_max_soc": today_projected_max_soc,
            "today_projected_sunset_soc": today_projected_sunset_soc,
            "today_predicted_export": today_predicted_export,
            "today_predicted_grid_import": today_predicted_grid_import,
            "today_discretionary_energy": today_discretionary_energy,
            "today_recommended_presolar_discharge": today_recommended_presolar_discharge,
            "today_projection_model": today_projection_model,
            "next_day_plan_date": next_day_date.isoformat(),
            "tomorrow_plan_ready": next_day_plan_ready,
            "tomorrow_strategy": next_day_strategy,
            "tomorrow_strategy_reason": next_day_strategy_reason,
            "required_headroom_tomorrow": required_headroom_next_day,
            "headroom_margin_tomorrow": headroom_margin_next_day,
            "headroom_shortfall_tomorrow": headroom_shortfall_next_day,
            "recommended_overnight_discharge": recommended_discharge_before_next_day,
            "projected_next_day_start_soc": projected_next_day_start_soc,
            "planned_next_day_start_soc": planned_next_day_start_soc,
            "projected_max_soc_tomorrow": projected_max_soc_next_day,
            "projected_sunset_soc_tomorrow": projected_sunset_soc_next_day,
            "predicted_export_tomorrow": predicted_export_next_day,
            "predicted_grid_import_tomorrow": predicted_grid_import_next_day,
            "discretionary_energy_tomorrow": discretionary_energy_next_day,
            "tomorrow_projection_model": next_day_projection_model,
        }

    async def async_apply_headroom_policy(self) -> None:
        """Refresh advisory policy state without actuating devices."""
        await self.async_request_refresh()
