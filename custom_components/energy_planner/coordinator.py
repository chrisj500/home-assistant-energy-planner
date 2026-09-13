from __future__ import annotations

from datetime import timedelta
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
    STRATEGY_INSUFFICIENT_DATA,
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


def _tomorrow_solar_window(hass: HomeAssistant):
    tomorrow = dt_util.now().date() + timedelta(days=1)
    sunrise = get_astral_event_date(hass, SUN_EVENT_SUNRISE, tomorrow)
    sunset = get_astral_event_date(hass, SUN_EVENT_SUNSET, tomorrow)
    return sunrise, sunset


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
        upcoming = solar_today if dt_util.now().hour < 12 else solar_tomorrow

        reserve = _num(self.hass, cfg.get(CONF_BACKUP_RESERVE))
        storm = _is_on(self.hass, cfg.get(CONF_STORM_WARNING))
        minimum_reserve = float(cfg.get(OPT_MIN_RESERVE, DEFAULT_MIN_RESERVE))
        strong_solar = float(cfg.get(OPT_STRONG_SOLAR_KWH, DEFAULT_STRONG_SOLAR_KWH))
        auto_headroom = bool(cfg.get(OPT_AUTO_HEADROOM, False))

        ready = reserve is not None and storm is not None and upcoming is not None

        projected_sunset_soc = None
        projected_charge_to_sunset = None
        projection_available_ac = None
        projection_model = None

        remaining_solar = _num(self.hass, cfg.get(CONF_SOLAR_REMAINING))
        expected_load = _num(self.hass, cfg.get(CONF_EXPECTED_LOAD_REMAINING))
        actual_solar_w = _num(self.hass, cfg.get(CONF_ACTUAL_SOLAR_POWER))
        peak_time = _timestamp(self.hass, cfg.get(CONF_SOLAR_PEAK_TIME))
        sunset = _next_sunset(self.hass)
        now = dt_util.now()

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
                harvest_capture_factor=float(
                    cfg.get(
                        OPT_HARVEST_CAPTURE_FACTOR,
                        DEFAULT_HARVEST_CAPTURE_FACTOR,
                    )
                ),
                charge_efficiency=float(
                    cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)
                ),
            )
            projected_sunset_soc = projection.projected_soc
            projected_charge_to_sunset = projection.projected_charge_kwh
            projection_available_ac = projection.projected_available_ac_kwh
            projection_model = projection.model
        elif weighted_soc is not None and sunset is None:
            projected_sunset_soc = weighted_soc
            projected_charge_to_sunset = 0.0
            projection_available_ac = 0.0
            projection_model = "after_sunset"

        ev_soc = _num(self.hass, cfg.get(CONF_EV_SOC))
        ev_home_entity = cfg.get(CONF_EV_HOME)
        ev_home = None
        if ev_home_entity:
            state = self.hass.states.get(ev_home_entity)
            ev_home = None if state is None else state.state == "home"
        ev_target = float(cfg.get(OPT_EV_TARGET_SOC, DEFAULT_EV_TARGET_SOC))

        base_load_power_w = _num(self.hass, cfg.get(CONF_BASE_LOAD_POWER))
        peak_time_tomorrow = _timestamp(
            self.hass, cfg.get(CONF_SOLAR_PEAK_TIME_TOMORROW)
        )
        tomorrow_sunrise, tomorrow_sunset = _tomorrow_solar_window(self.hass)

        tomorrow_strategy = STRATEGY_INSUFFICIENT_DATA
        tomorrow_strategy_reason = "Tomorrow planning inputs are incomplete."
        tomorrow_plan_ready = False
        required_headroom_tomorrow = None
        headroom_margin_tomorrow = None
        headroom_shortfall_tomorrow = None
        recommended_overnight_discharge = None
        projected_max_soc_tomorrow = None
        projected_sunset_soc_tomorrow = None
        predicted_export_tomorrow = None
        predicted_grid_import_tomorrow = None
        discretionary_energy_tomorrow = None
        tomorrow_projection_model = None

        tomorrow_required_values = {
            "battery SOC": weighted_soc,
            "charge limit": charge_limit,
            "tomorrow solar forecast": solar_tomorrow,
            "base load power": base_load_power_w,
            "storm state": storm,
            "tomorrow sunrise": tomorrow_sunrise,
            "tomorrow sunset": tomorrow_sunset,
        }
        missing_tomorrow = [
            label for label, value in tomorrow_required_values.items() if value is None
        ]

        if not missing_tomorrow:
            plan = plan_solar_period(
                sunrise=tomorrow_sunrise,
                sunset=tomorrow_sunset,
                current_soc_pct=weighted_soc,
                capacity_kwh=capacity,
                charge_limit_pct=charge_limit,
                minimum_reserve_pct=minimum_reserve,
                solar_forecast_kwh=solar_tomorrow,
                base_load_power_w=base_load_power_w,
                peak_time=peak_time_tomorrow,
                harvest_capture_factor=float(
                    cfg.get(
                        OPT_HARVEST_CAPTURE_FACTOR,
                        DEFAULT_HARVEST_CAPTURE_FACTOR,
                    )
                ),
                charge_efficiency=float(
                    cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)
                ),
                storm_active=storm,
                ev_soc_pct=ev_soc,
                ev_target_soc_pct=ev_target,
                ev_home=ev_home,
                discretionary_threshold_kwh=float(
                    cfg.get(
                        OPT_DISCRETIONARY_THRESHOLD_KWH,
                        DEFAULT_DISCRETIONARY_THRESHOLD_KWH,
                    )
                ),
            )
            tomorrow_strategy = plan.strategy
            tomorrow_strategy_reason = plan.reason
            tomorrow_plan_ready = True
            required_headroom_tomorrow = plan.required_headroom_kwh
            headroom_margin_tomorrow = plan.headroom_margin_kwh
            headroom_shortfall_tomorrow = plan.headroom_shortfall_kwh
            recommended_overnight_discharge = (
                plan.recommended_overnight_discharge_kwh
            )
            projected_max_soc_tomorrow = plan.projected_max_soc_pct
            projected_sunset_soc_tomorrow = plan.projected_sunset_soc_pct
            predicted_export_tomorrow = plan.predicted_export_kwh
            predicted_grid_import_tomorrow = plan.predicted_grid_import_kwh
            discretionary_energy_tomorrow = plan.discretionary_energy_kwh
            tomorrow_projection_model = plan.model
        elif missing_tomorrow:
            tomorrow_strategy_reason = (
                "Missing: " + ", ".join(missing_tomorrow) + "."
            )

        # v0.1.6 is advisory-only: expose whether headroom creation would be
        # recommended, but do not actuate the backup reserve or EcoFlow mode.
        release = bool(
            auto_headroom
            and tomorrow_plan_ready
            and tomorrow_strategy == STRATEGY_CREATE_HEADROOM
            and not storm
        )

        if ev_soc is None:
            ev_plan = "EV SOC unavailable"
        elif ev_home is False:
            ev_plan = "EV away"
        elif ev_soc >= ev_target - 1:
            ev_plan = "No charge needed"
        elif tomorrow_strategy == STRATEGY_USE_DISCRETIONARY_LOADS:
            ev_plan = "Charge during tomorrow solar window"
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
            "tomorrow_plan_ready": tomorrow_plan_ready,
            "tomorrow_strategy": tomorrow_strategy,
            "tomorrow_strategy_reason": tomorrow_strategy_reason,
            "required_headroom_tomorrow": required_headroom_tomorrow,
            "headroom_margin_tomorrow": headroom_margin_tomorrow,
            "headroom_shortfall_tomorrow": headroom_shortfall_tomorrow,
            "recommended_overnight_discharge": recommended_overnight_discharge,
            "projected_max_soc_tomorrow": projected_max_soc_tomorrow,
            "projected_sunset_soc_tomorrow": projected_sunset_soc_tomorrow,
            "predicted_export_tomorrow": predicted_export_tomorrow,
            "predicted_grid_import_tomorrow": predicted_grid_import_tomorrow,
            "discretionary_energy_tomorrow": discretionary_energy_tomorrow,
            "tomorrow_projection_model": tomorrow_projection_model,
        }

    async def async_apply_headroom_policy(self) -> None:
        """Refresh advisory policy state without actuating devices in v0.1.6."""
        await self.async_request_refresh()
