from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant, State
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
    DEFAULT_EV_SOLAR_ADVISORY_ENABLED,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_MIN_RESERVE,
    DEFAULT_PREFERRED_IMPORT_W,
    DEFAULT_WEIGHTS,
    OPT_AUTO_HEADROOM,
    OPT_CHARGE_EFFICIENCY,
    OPT_DISCRETIONARY_THRESHOLD_KWH,
    OPT_EV_SOLAR_ADVISORY_ENABLED,
    OPT_EV_TARGET_SOC,
    OPT_MIN_RESERVE,
    OPT_PREFERRED_IMPORT_W,
)
from .projection import project_sunset_soc
from .simulation import ControllerSettings
from .strategy import (
    STRATEGY_CREATE_HEADROOM,
    STRATEGY_HOLD,
    STRATEGY_INSUFFICIENT_DATA,
    STRATEGY_PRESERVE_FOR_RESILIENCE,
    STRATEGY_USE_DISCRETIONARY_LOADS,
    plan_solar_period,
)

_LOGGER = logging.getLogger(__name__)
INVALID_STATES = {"unknown", "unavailable", "none", ""}

_CONTROLLER_DEFAULTS: dict[str, float | str] = {
    "operating_mode": "observe",
    "minimum_rate_w": 500.0,
    "maximum_rate_w": 3900.0,
    "rate_step_w": 100.0,
    "maximum_rate_increase_w": 800.0,
    "slow_import_decrease_w": 200.0,
    "moderate_import_decrease_w": 500.0,
    "preferred_import_w": 250.0,
    "import_hold_high_w": 350.0,
    "moderate_import_threshold_w": 1000.0,
    "severe_import_threshold_w": 2000.0,
    "start_export_w": 150.0,
    "export_gain": 1.0,
    "import_gain": 0.8,
    "minimum_solar_w": 150.0,
    "stop_all_w": 250.0,
    "start_2_w": 1800.0,
    "stop_2_w": 1100.0,
    "start_3_w": 3300.0,
    "stop_3_w": 2400.0,
}


def _num(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in INVALID_STATES:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _attr_float(state: State | None, key: str) -> float | None:
    if state is None:
        return None
    try:
        value = state.attributes.get(key)
        return None if value is None else float(value)
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
    if state is None or state.state in INVALID_STATES:
        return None
    return dt_util.parse_datetime(state.state)


def _solar_window(hass: HomeAssistant, target_date: date):
    return (
        get_astral_event_date(hass, SUN_EVENT_SUNRISE, target_date),
        get_astral_event_date(hass, SUN_EVENT_SUNSET, target_date),
    )


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


def _option_float(options: dict[str, Any], key: str) -> float:
    try:
        return float(options.get(key, _CONTROLLER_DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(_CONTROLLER_DEFAULTS[key])


def _controller_settings(
    hass: HomeAssistant,
    fallback_preferred_import_w: float,
) -> ControllerSettings:
    """Mirror the installed surplus controller instead of duplicating settings."""
    entries = hass.config_entries.async_entries("ecoflow_solar_surplus")
    if not entries:
        return ControllerSettings(
            preferred_import_w=max(float(fallback_preferred_import_w), 0.0),
            enabled=False,
            source="controller_unavailable",
        )

    entry = entries[0]
    options = dict(entry.options)
    configured_min = _option_float(options, "minimum_rate_w")
    configured_max = _option_float(options, "maximum_rate_w")
    configured_step = _option_float(options, "rate_step_w")

    charging_entity = entry.data.get("charging_power")
    charging_state = hass.states.get(charging_entity) if charging_entity else None
    hardware_min = _attr_float(charging_state, "min")
    hardware_max = _attr_float(charging_state, "max")
    hardware_step = _attr_float(charging_state, "step")

    minimum = max(configured_min, hardware_min or configured_min)
    maximum = min(configured_max, hardware_max or configured_max)
    if maximum < minimum:
        maximum = minimum
    step = hardware_step if hardware_step and hardware_step > 0 else configured_step
    if step <= 0:
        step = 100.0

    enabled = str(options.get("operating_mode", "observe")) == "control"
    return ControllerSettings(
        minimum_rate_w=minimum,
        maximum_rate_w=maximum,
        rate_step_w=step,
        maximum_rate_increase_w=_option_float(options, "maximum_rate_increase_w"),
        slow_import_decrease_w=_option_float(options, "slow_import_decrease_w"),
        moderate_import_decrease_w=_option_float(options, "moderate_import_decrease_w"),
        preferred_import_w=_option_float(options, "preferred_import_w"),
        import_hold_high_w=_option_float(options, "import_hold_high_w"),
        moderate_import_threshold_w=_option_float(options, "moderate_import_threshold_w"),
        severe_import_threshold_w=_option_float(options, "severe_import_threshold_w"),
        start_export_w=_option_float(options, "start_export_w"),
        export_gain=_option_float(options, "export_gain"),
        import_gain=_option_float(options, "import_gain"),
        minimum_solar_w=_option_float(options, "minimum_solar_w"),
        stop_all_w=_option_float(options, "stop_all_w"),
        start_2_w=_option_float(options, "start_2_w"),
        stop_2_w=_option_float(options, "stop_2_w"),
        start_3_w=_option_float(options, "start_3_w"),
        stop_3_w=_option_float(options, "stop_3_w"),
        enabled=enabled,
        source="ecoflow_solar_surplus_control" if enabled else "ecoflow_solar_surplus_observe",
    )


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

        soc_values = [
            _num(self.hass, cfg.get(CONF_SOC_1)),
            _num(self.hass, cfg.get(CONF_SOC_2)),
            _num(self.hass, cfg.get(CONF_SOC_3)),
        ]
        bank_socs = (
            tuple(float(value) for value in soc_values)
            if all(value is not None for value in soc_values)
            else None
        )

        capacity = float(cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        total_weight = sum(weights)
        bank_capacities = tuple(capacity * weight / total_weight for weight in weights)
        weighted_soc = None
        if bank_socs is not None:
            weighted_soc = sum(
                soc * bank_capacity
                for soc, bank_capacity in zip(bank_socs, bank_capacities)
            ) / capacity

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
        configured_minimum_reserve = float(cfg.get(OPT_MIN_RESERVE, DEFAULT_MIN_RESERVE))
        effective_reserve = max(configured_minimum_reserve, reserve or configured_minimum_reserve)
        auto_headroom = bool(cfg.get(OPT_AUTO_HEADROOM, False))
        charge_efficiency = float(cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY))
        discretionary_threshold = float(
            cfg.get(OPT_DISCRETIONARY_THRESHOLD_KWH, DEFAULT_DISCRETIONARY_THRESHOLD_KWH)
        )
        ev_solar_advisory = bool(
            cfg.get(OPT_EV_SOLAR_ADVISORY_ENABLED, DEFAULT_EV_SOLAR_ADVISORY_ENABLED)
        )
        controller = _controller_settings(
            self.hass,
            float(cfg.get(OPT_PREFERRED_IMPORT_W, DEFAULT_PREFERRED_IMPORT_W)),
        )

        ready = reserve is not None and storm is not None and upcoming is not None

        remaining_solar = _num(self.hass, cfg.get(CONF_SOLAR_REMAINING))
        expected_load = _num(self.hass, cfg.get(CONF_EXPECTED_LOAD_REMAINING))
        actual_solar_w = _num(self.hass, cfg.get(CONF_ACTUAL_SOLAR_POWER))
        peak_time = _timestamp(self.hass, cfg.get(CONF_SOLAR_PEAK_TIME))
        peak_time_tomorrow = _timestamp(self.hass, cfg.get(CONF_SOLAR_PEAK_TIME_TOMORROW))
        base_load_power_w = _num(self.hass, cfg.get(CONF_BASE_LOAD_POWER))

        ev_soc = _num(self.hass, cfg.get(CONF_EV_SOC))
        ev_home_entity = cfg.get(CONF_EV_HOME)
        ev_home = None
        if ev_home_entity:
            state = self.hass.states.get(ev_home_entity)
            ev_home = None if state is None else state.state == "home"
        ev_target = float(cfg.get(OPT_EV_TARGET_SOC, DEFAULT_EV_TARGET_SOC))

        projected_sunset_soc = None
        projected_charge_to_sunset = None
        projection_available_ac = None
        projection_model = None
        projection = None
        sunset = _next_sunset(self.hass)
        projection_inputs_ready = all(
            value is not None
            for value in (
                weighted_soc,
                bank_socs,
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
                charge_efficiency=charge_efficiency,
                bank_socs_pct=bank_socs,
                bank_capacities_kwh=bank_capacities,
                controller_settings=controller,
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

        before_sunrise = today_sunrise is not None and now < today_sunrise
        after_sunset = today_sunset is not None and now >= today_sunset
        daylight = (
            today_sunrise is not None
            and today_sunset is not None
            and today_sunrise <= now < today_sunset
        )

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
        today_ending_bank_socs = None
        today_capacity_export = None
        today_power_export = None
        today_control_export = None
        today_grid_to_battery = None
        today_peak_export_w = None
        today_export_minutes = None

        if before_sunrise:
            required = {
                "battery SOC": weighted_soc,
                "bank SOCs": bank_socs,
                "charge limit": charge_limit,
                "today solar forecast": solar_today,
                "base load power": base_load_power_w,
                "storm state": storm,
                "today sunrise": today_sunrise,
                "today sunset": today_sunset,
            }
            missing = [label for label, value in required.items() if value is None]
            if not missing:
                plan = plan_solar_period(
                    sunrise=today_sunrise,
                    sunset=today_sunset,
                    current_soc_pct=weighted_soc,
                    capacity_kwh=capacity,
                    charge_limit_pct=charge_limit,
                    minimum_reserve_pct=effective_reserve,
                    solar_forecast_kwh=solar_today,
                    base_load_power_w=base_load_power_w,
                    peak_time=peak_time,
                    charge_efficiency=charge_efficiency,
                    storm_active=storm,
                    ev_soc_pct=ev_soc,
                    ev_target_soc_pct=ev_target,
                    ev_home=ev_home,
                    ev_discretionary_allowed=ev_solar_advisory,
                    discretionary_threshold_kwh=discretionary_threshold,
                    allow_presolar_discharge=True,
                    bank_socs_pct=bank_socs,
                    bank_capacities_kwh=bank_capacities,
                    controller_settings=controller,
                )
                today_plan_ready = True
                today_strategy = plan.strategy
                today_strategy_reason = plan.reason
                today_projected_max_soc = plan.projected_max_soc_pct
                today_projected_sunset_soc = plan.projected_sunset_soc_pct
                today_predicted_export = plan.predicted_export_kwh
                today_predicted_grid_import = plan.predicted_grid_import_kwh
                today_discretionary_energy = plan.discretionary_energy_kwh
                today_recommended_presolar_discharge = plan.recommended_overnight_discharge_kwh
                today_projection_model = plan.model
                today_ending_bank_socs = plan.ending_bank_socs_pct
                today_capacity_export = plan.capacity_limited_export_kwh
                today_power_export = plan.power_limited_export_kwh
                today_control_export = plan.control_limited_export_kwh
                today_grid_to_battery = plan.grid_to_battery_ac_kwh
                today_peak_export_w = plan.peak_export_w
                today_export_minutes = plan.export_minutes
            else:
                today_strategy_reason = "Missing: " + ", ".join(missing) + "."
        elif daylight and projection is not None:
            today_plan_ready = True
            today_projected_max_soc = projection.projected_soc
            today_projected_sunset_soc = projection.projected_soc
            today_predicted_export = projection.predicted_export_kwh
            today_predicted_grid_import = projection.predicted_grid_import_kwh
            today_discretionary_energy = (
                projection.predicted_export_kwh
                if projection.predicted_export_kwh >= discretionary_threshold
                else 0.0
            )
            today_recommended_presolar_discharge = 0.0
            today_projection_model = f"live_{projection.model}"
            today_ending_bank_socs = projection.ending_bank_socs_pct
            today_capacity_export = projection.capacity_limited_export_kwh
            today_power_export = projection.power_limited_export_kwh
            today_control_export = projection.control_limited_export_kwh
            today_grid_to_battery = projection.grid_to_battery_ac_kwh
            today_peak_export_w = projection.peak_export_w
            today_export_minutes = projection.export_minutes
            if storm:
                today_strategy = STRATEGY_PRESERVE_FOR_RESILIENCE
                today_strategy_reason = "Storm protection is active; preserve stored energy today."
            elif projection.predicted_export_kwh >= discretionary_threshold:
                today_strategy = STRATEGY_USE_DISCRETIONARY_LOADS
                today_strategy_reason = (
                    f"About {projection.predicted_export_kwh:.2f} kWh remains at physical "
                    "export risk; daylight battery discharge is prohibited. Use only a "
                    "controllable flexible load if needed."
                )
            else:
                today_strategy = STRATEGY_HOLD
                today_strategy_reason = (
                    f"Physical remaining export risk is only "
                    f"{projection.predicted_export_kwh:.2f} kWh; preserve stored solar."
                )
        elif after_sunset and weighted_soc is not None and bank_socs is not None:
            today_plan_ready = True
            today_strategy = STRATEGY_HOLD
            today_strategy_reason = "Today's solar window is complete; preserve stored solar overnight."
            today_projected_max_soc = weighted_soc
            today_projected_sunset_soc = weighted_soc
            today_predicted_export = 0.0
            today_predicted_grid_import = 0.0
            today_discretionary_energy = 0.0
            today_recommended_presolar_discharge = 0.0
            today_projection_model = "day_complete"
            today_ending_bank_socs = bank_socs
            today_capacity_export = 0.0
            today_power_export = 0.0
            today_control_export = 0.0
            today_grid_to_battery = 0.0
            today_peak_export_w = 0.0
            today_export_minutes = 0.0

        projected_next_day_start_soc = today_projected_sunset_soc
        projected_next_day_bank_socs = today_ending_bank_socs
        if projected_next_day_start_soc is None and after_sunset:
            projected_next_day_start_soc = weighted_soc
            projected_next_day_bank_socs = bank_socs

        next_day_plan_ready = False
        next_day_strategy = STRATEGY_INSUFFICIENT_DATA
        next_day_strategy_reason = "Next-day planning inputs are incomplete."
        required_headroom_next_day = None
        headroom_margin_next_day = None
        headroom_shortfall_next_day = None
        recommended_discharge_before_next_day = None
        planned_next_day_start_soc = None
        projected_max_soc_next_day = None
        projected_sunset_soc_next_day = None
        predicted_export_next_day = None
        predicted_grid_import_next_day = None
        discretionary_energy_next_day = None
        next_day_projection_model = None
        next_day_capacity_export = None
        next_day_power_export = None
        next_day_control_export = None
        next_day_grid_to_battery = None
        next_day_peak_export_w = None
        next_day_export_minutes = None

        required_next = {
            "projected start SOC": projected_next_day_start_soc,
            "projected bank SOCs": projected_next_day_bank_socs,
            "charge limit": charge_limit,
            "next-day solar forecast": solar_tomorrow,
            "base load power": base_load_power_w,
            "storm state": storm,
            "next-day sunrise": next_day_sunrise,
            "next-day sunset": next_day_sunset,
        }
        missing_next = [label for label, value in required_next.items() if value is None]
        if not missing_next:
            plan = plan_solar_period(
                sunrise=next_day_sunrise,
                sunset=next_day_sunset,
                current_soc_pct=projected_next_day_start_soc,
                capacity_kwh=capacity,
                charge_limit_pct=charge_limit,
                minimum_reserve_pct=effective_reserve,
                solar_forecast_kwh=solar_tomorrow,
                base_load_power_w=base_load_power_w,
                peak_time=peak_time_tomorrow,
                charge_efficiency=charge_efficiency,
                storm_active=storm,
                ev_soc_pct=ev_soc,
                ev_target_soc_pct=ev_target,
                ev_home=ev_home,
                ev_discretionary_allowed=ev_solar_advisory,
                discretionary_threshold_kwh=discretionary_threshold,
                allow_presolar_discharge=True,
                bank_socs_pct=projected_next_day_bank_socs,
                bank_capacities_kwh=bank_capacities,
                controller_settings=controller,
            )
            next_day_plan_ready = True
            next_day_strategy = plan.strategy
            next_day_strategy_reason = plan.reason
            required_headroom_next_day = plan.required_headroom_kwh
            headroom_margin_next_day = plan.headroom_margin_kwh
            headroom_shortfall_next_day = plan.headroom_shortfall_kwh
            recommended_discharge_before_next_day = plan.recommended_overnight_discharge_kwh
            planned_next_day_start_soc = plan.planned_start_soc_pct
            projected_max_soc_next_day = plan.projected_max_soc_pct
            projected_sunset_soc_next_day = plan.projected_sunset_soc_pct
            predicted_export_next_day = plan.predicted_export_kwh
            predicted_grid_import_next_day = plan.predicted_grid_import_kwh
            discretionary_energy_next_day = plan.discretionary_energy_kwh
            next_day_projection_model = plan.model
            next_day_capacity_export = plan.capacity_limited_export_kwh
            next_day_power_export = plan.power_limited_export_kwh
            next_day_control_export = plan.control_limited_export_kwh
            next_day_grid_to_battery = plan.grid_to_battery_ac_kwh
            next_day_peak_export_w = plan.peak_export_w
            next_day_export_minutes = plan.export_minutes
        else:
            next_day_strategy_reason = "Missing: " + ", ".join(missing_next) + "."

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
        elif not ev_solar_advisory:
            ev_plan = "Solar EV advisory disabled; avoid unmanaged surplus charging"
        elif today_strategy == STRATEGY_USE_DISCRETIONARY_LOADS:
            ev_plan = "Controlled solar charging opportunity today"
        elif next_day_strategy == STRATEGY_USE_DISCRETIONARY_LOADS:
            ev_plan = "Controlled solar charging opportunity next day"
        else:
            ev_plan = "Grid charge when convenient"

        return {
            "weighted_soc": weighted_soc,
            "stored_energy": stored,
            "battery_headroom": headroom,
            "upcoming_solar": upcoming,
            "reserve": reserve,
            "effective_reserve_floor": effective_reserve,
            "storm": storm,
            "control_ready": ready,
            "headroom_release": release,
            "controller_model_source": controller.source,
            "controller_model_enabled": controller.enabled,
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
            "today_capacity_limited_export": today_capacity_export,
            "today_power_limited_export": today_power_export,
            "today_control_limited_export": today_control_export,
            "today_grid_to_battery": today_grid_to_battery,
            "today_peak_export_w": today_peak_export_w,
            "today_export_minutes": today_export_minutes,
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
            "next_day_capacity_limited_export": next_day_capacity_export,
            "next_day_power_limited_export": next_day_power_export,
            "next_day_control_limited_export": next_day_control_export,
            "next_day_grid_to_battery": next_day_grid_to_battery,
            "next_day_peak_export_w": next_day_peak_export_w,
            "next_day_export_minutes": next_day_export_minutes,
        }

    async def async_apply_headroom_policy(self) -> None:
        """Refresh advisory policy state without actuating devices."""
        await self.async_request_refresh()
