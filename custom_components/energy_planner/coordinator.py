from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .calibration import (
    CalibrationProfile,
    HeadroomDecision,
    append_record,
    apply_stored_energy_drop,
    build_profile,
    confidence_headroom_decision,
    projected_overnight_drop_kwh,
)
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
    SolarPeriodPlan,
    plan_solar_period,
)

_LOGGER = logging.getLogger(__name__)
INVALID_STATES = {"unknown", "unavailable", "none", ""}
_CALIBRATION_STORE_VERSION = 1

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
    """Use the installed capture integration only for physical capabilities."""
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
        source=(
            "ecoflow_solar_surplus_control"
            if enabled
            else "ecoflow_solar_surplus_observe"
        ),
    )


def _stored_energy(
    socs: tuple[float, float, float],
    capacities: tuple[float, float, float],
) -> float:
    return sum(capacity * soc / 100.0 for capacity, soc in zip(capacities, socs))


def _weighted_soc(
    socs: tuple[float, float, float],
    capacities: tuple[float, float, float],
) -> float:
    return 100.0 * _stored_energy(socs, capacities) / sum(capacities)


def _ev_available(
    ev_soc: float | None,
    ev_target: float,
    ev_home: bool | None,
) -> bool:
    return (
        ev_soc is not None
        and ev_soc < ev_target - 1.0
        and ev_home is not False
    )


def _confidence_strategy(
    *,
    nominal: SolarPeriodPlan,
    decision: HeadroomDecision,
    storm: bool,
    controller_enabled: bool,
    discretionary_threshold_kwh: float,
    ev_solar_advisory: bool,
    ev_available: bool,
) -> tuple[str, str]:
    """Select only actions justified by physics plus measured forecast error."""
    if storm:
        return (
            STRATEGY_PRESERVE_FOR_RESILIENCE,
            "Storm protection is active; preserve stored energy and do not create headroom.",
        )

    if not controller_enabled and nominal.predicted_export_kwh >= discretionary_threshold_kwh:
        return (
            STRATEGY_HOLD,
            f"Point forecast shows {nominal.predicted_export_kwh:.2f} kWh unabsorbed "
            "because capture control is unavailable. Restore the Solar Surplus "
            "controller rather than cycling stored energy.",
        )

    capacity_risk = nominal.capacity_limited_export_kwh >= discretionary_threshold_kwh
    power_risk = nominal.power_limited_export_kwh >= discretionary_threshold_kwh

    if capacity_risk and ev_solar_advisory and ev_available:
        return (
            STRATEGY_USE_DISCRETIONARY_LOADS,
            f"Baseline capacity-limited export is {nominal.capacity_limited_export_kwh:.2f} "
            "kWh. A reliably controllable flexible load is preferred before stationary "
            "battery cycling.",
        )

    if capacity_risk:
        if not decision.action_ready:
            return (
                STRATEGY_HOLD,
                f"Baseline point forecast shows {nominal.capacity_limited_export_kwh:.2f} "
                f"kWh of capacity-limited export, but {decision.reason}",
            )
        if decision.recommended_additional_discharge_kwh > 0.05:
            return (
                STRATEGY_CREATE_HEADROOM,
                f"Baseline point forecast shows {nominal.capacity_limited_export_kwh:.2f} "
                "kWh of capacity-limited export. "
                f"{decision.reason} Recommend only "
                f"{decision.recommended_additional_discharge_kwh:.2f} kWh of additional "
                "pre-solar discharge.",
            )
        if decision.confidence_shortfall_kwh <= 0.05:
            return (
                STRATEGY_HOLD,
                f"Baseline point forecast shows {nominal.capacity_limited_export_kwh:.2f} "
                "kWh of capacity-limited export, but the no-regret confidence model "
                "does not justify additional battery discharge. Preserve stored solar.",
            )
        return (
            STRATEGY_HOLD,
            "Additional headroom would be useful under the confidence model, but the "
            "effective reserve floor prevents the required discharge.",
        )

    if power_risk:
        if ev_solar_advisory and ev_available:
            return (
                STRATEGY_USE_DISCRETIONARY_LOADS,
                f"About {nominal.power_limited_export_kwh:.2f} kWh is physically "
                "power-limited rather than capacity-limited; use only a reliably "
                "controllable flexible load.",
            )
        return (
            STRATEGY_HOLD,
            f"About {nominal.power_limited_export_kwh:.2f} kWh is physically "
            "power-limited. Extra battery discharge cannot fix a charging-power "
            "constraint, and unmanaged EV charging remains disabled.",
        )

    return (
        STRATEGY_HOLD,
        f"No material unavoidable export is forecast ({nominal.predicted_export_kwh:.2f} "
        "kWh); preserve stored solar.",
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
        self._calibration_store: Store[dict[str, Any]] = Store(
            hass,
            _CALIBRATION_STORE_VERSION,
            f"energy_planner.{entry.entry_id}.calibration",
        )
        self._calibration_data: dict[str, Any] | None = None

    @property
    def cfg(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    async def _ensure_calibration_data(self) -> dict[str, Any]:
        if self._calibration_data is None:
            loaded = await self._calibration_store.async_load()
            self._calibration_data = loaded if isinstance(loaded, dict) else {}
        self._calibration_data.setdefault("daylight_records", [])
        self._calibration_data.setdefault("overnight_records", [])
        return self._calibration_data

    async def _save_calibration_data(self) -> None:
        if self._calibration_data is not None:
            await self._calibration_store.async_save(self._calibration_data)

    async def _finalize_calibration_history(
        self,
        *,
        now,
        today_date: date,
        today_sunrise,
        before_sunrise: bool,
        after_sunset: bool,
        stored_kwh: float | None,
    ) -> None:
        data = await self._ensure_calibration_data()
        changed = False

        daylight_pending = data.get("daylight_pending")
        if isinstance(daylight_pending, dict):
            pending_date = str(daylight_pending.get("date", ""))
            if pending_date < today_date.isoformat():
                # If HA missed sunset we cannot distinguish daylight gain from
                # overnight discharge. Drop the sample rather than contaminate
                # calibration history.
                data.pop("daylight_pending", None)
                changed = True
            elif after_sunset and pending_date == today_date.isoformat() and stored_kwh is not None:
                try:
                    start_stored = float(daylight_pending["start_stored_kwh"])
                    predicted_gain = float(daylight_pending["predicted_gain_kwh"])
                except (KeyError, TypeError, ValueError):
                    start_stored = 0.0
                    predicted_gain = 0.0
                if predicted_gain >= 0.5:
                    actual_gain = stored_kwh - start_stored
                    error = actual_gain - predicted_gain
                    ratio = error / predicted_gain
                    records = list(data.get("daylight_records", []))
                    data["daylight_records"] = append_record(
                        records,
                        {
                            "date": pending_date,
                            "predicted_gain_kwh": predicted_gain,
                            "actual_gain_kwh": actual_gain,
                            "error_kwh": error,
                            "error_ratio": ratio,
                        },
                    )
                data.pop("daylight_pending", None)
                changed = True

        overnight_pending = data.get("overnight_pending")
        near_sunrise = (
            before_sunrise
            and today_sunrise is not None
            and 0 <= (today_sunrise - now).total_seconds() <= 15 * 60
        )
        if isinstance(overnight_pending, dict) and near_sunrise and stored_kwh is not None:
            try:
                start_stored = float(overnight_pending["start_stored_kwh"])
                started = dt_util.parse_datetime(str(overnight_pending["start_time"]))
                valid = bool(overnight_pending.get("valid_for_calibration", True))
            except (KeyError, TypeError, ValueError):
                start_stored = 0.0
                started = None
                valid = False
            if started is not None:
                hours = max((now - started).total_seconds() / 3600.0, 0.0)
                if valid and hours >= 1.0:
                    drop = max(start_stored - stored_kwh, 0.0)
                    records = list(data.get("overnight_records", []))
                    data["overnight_records"] = append_record(
                        records,
                        {
                            "date": today_date.isoformat(),
                            "hours": hours,
                            "drop_kwh": drop,
                            "drop_rate_kw": drop / hours,
                        },
                    )
            data.pop("overnight_pending", None)
            changed = True

        if changed:
            await self._save_calibration_data()

    async def _capture_daylight_forecast(
        self,
        *,
        target_date: date,
        predicted_gain_kwh: float,
    ) -> None:
        data = await self._ensure_calibration_data()
        pending = data.get("daylight_pending")
        if isinstance(pending, dict) and pending.get("date") == target_date.isoformat():
            return
        data["daylight_pending"] = {
            "date": target_date.isoformat(),
            "predicted_gain_kwh": max(float(predicted_gain_kwh), 0.0),
        }
        await self._save_calibration_data()

    async def _start_daylight_sample(
        self,
        *,
        target_date: date,
        stored_kwh: float | None,
    ) -> None:
        if stored_kwh is None:
            return
        data = await self._ensure_calibration_data()
        pending = data.get("daylight_pending")
        if not isinstance(pending, dict) or pending.get("date") != target_date.isoformat():
            return
        if "start_stored_kwh" in pending:
            return
        pending["start_stored_kwh"] = float(stored_kwh)
        pending["start_time"] = dt_util.now().isoformat()
        await self._save_calibration_data()

    async def _capture_overnight_start(
        self,
        *,
        now,
        stored_kwh: float | None,
        valid_for_calibration: bool,
    ) -> None:
        if stored_kwh is None:
            return
        data = await self._ensure_calibration_data()
        if isinstance(data.get("overnight_pending"), dict):
            return
        data["overnight_pending"] = {
            "start_time": now.isoformat(),
            "start_stored_kwh": float(stored_kwh),
            "valid_for_calibration": bool(valid_for_calibration),
        }
        await self._save_calibration_data()

    async def _async_update_data(self) -> dict[str, Any]:
        cfg = self.cfg
        now = dt_util.now()
        today_date = now.date()
        next_day_date = today_date + timedelta(days=1)
        today_sunrise, today_sunset = _solar_window(self.hass, today_date)
        next_day_sunrise, next_day_sunset = _solar_window(self.hass, next_day_date)

        before_sunrise = today_sunrise is not None and now < today_sunrise
        after_sunset = today_sunset is not None and now >= today_sunset
        daylight = (
            today_sunrise is not None
            and today_sunset is not None
            and today_sunrise <= now < today_sunset
        )

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
        weighted_soc = _weighted_soc(bank_socs, bank_capacities) if bank_socs else None
        stored = _stored_energy(bank_socs, bank_capacities) if bank_socs else None
        charge_limit = _num(self.hass, cfg.get(CONF_CHARGE_LIMIT))
        headroom = (
            max(capacity * charge_limit / 100.0 - stored, 0.0)
            if stored is not None and charge_limit is not None
            else None
        )

        await self._finalize_calibration_history(
            now=now,
            today_date=today_date,
            today_sunrise=today_sunrise,
            before_sunrise=before_sunrise,
            after_sunset=after_sunset,
            stored_kwh=stored,
        )
        calibration_data = await self._ensure_calibration_data()
        profile = build_profile(
            calibration_data.get("daylight_records", []),
            calibration_data.get("overnight_records", []),
        )

        solar_today = _num(self.hass, cfg.get(CONF_SOLAR_TODAY))
        solar_tomorrow = _num(self.hass, cfg.get(CONF_SOLAR_TOMORROW))
        upcoming = solar_today if now.hour < 12 else solar_tomorrow
        reserve = _num(self.hass, cfg.get(CONF_BACKUP_RESERVE))
        storm_state = _is_on(self.hass, cfg.get(CONF_STORM_WARNING))
        storm = bool(storm_state)
        configured_minimum_reserve = float(cfg.get(OPT_MIN_RESERVE, DEFAULT_MIN_RESERVE))
        effective_reserve = max(
            configured_minimum_reserve,
            reserve if reserve is not None else configured_minimum_reserve,
        )
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
        ready = reserve is not None and storm_state is not None and upcoming is not None

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
        ev_available = _ev_available(ev_soc, ev_target, ev_home)

        # Live remaining-day projection remains independent of day-ahead
        # confidence calibration; it uses current production and corrected
        # remaining energy and still never permits daytime battery discharge.
        projection = None
        projected_sunset_soc = None
        projected_charge_to_sunset = None
        projection_available_ac = None
        projection_model = None
        sunset = _next_sunset(self.hass)
        if all(
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
        ):
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

        today_plan_ready = False
        today_strategy = STRATEGY_INSUFFICIENT_DATA
        today_strategy_reason = "Today's planning inputs are incomplete."
        today_plan: SolarPeriodPlan | None = None
        today_nominal: SolarPeriodPlan | None = None
        today_decision: HeadroomDecision | None = None
        today_ending_bank_socs = None
        today_recommended_discharge = 0.0

        if before_sunrise:
            required = (
                weighted_soc,
                bank_socs,
                charge_limit,
                solar_today,
                base_load_power_w,
                storm_state,
                today_sunrise,
                today_sunset,
            )
            if all(value is not None for value in required):
                today_nominal = plan_solar_period(
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
                    discretionary_threshold_kwh=discretionary_threshold,
                    allow_presolar_discharge=False,
                    bank_socs_pct=bank_socs,
                    bank_capacities_kwh=bank_capacities,
                    controller_settings=controller,
                )
                conservative_available = today_nominal.available_headroom_kwh
                stored_above_reserve = max(
                    stored - capacity * effective_reserve / 100.0,
                    0.0,
                )
                today_decision = confidence_headroom_decision(
                    profile=profile,
                    nominal_required_headroom_kwh=today_nominal.required_headroom_kwh,
                    conservative_available_headroom_kwh=conservative_available,
                    stored_above_reserve_kwh=stored_above_reserve,
                )
                today_strategy, today_strategy_reason = _confidence_strategy(
                    nominal=today_nominal,
                    decision=today_decision,
                    storm=storm,
                    controller_enabled=controller.enabled,
                    discretionary_threshold_kwh=discretionary_threshold,
                    ev_solar_advisory=ev_solar_advisory,
                    ev_available=ev_available,
                )
                today_recommended_discharge = (
                    today_decision.recommended_additional_discharge_kwh
                    if today_strategy == STRATEGY_CREATE_HEADROOM
                    else 0.0
                )
                planned_banks = apply_stored_energy_drop(
                    bank_socs_pct=bank_socs,
                    bank_capacities_kwh=bank_capacities,
                    drop_kwh=today_recommended_discharge,
                    reserve_pct=effective_reserve,
                )
                today_plan = plan_solar_period(
                    sunrise=today_sunrise,
                    sunset=today_sunset,
                    current_soc_pct=_weighted_soc(planned_banks, bank_capacities),
                    capacity_kwh=capacity,
                    charge_limit_pct=charge_limit,
                    minimum_reserve_pct=effective_reserve,
                    solar_forecast_kwh=solar_today,
                    base_load_power_w=base_load_power_w,
                    peak_time=peak_time,
                    charge_efficiency=charge_efficiency,
                    storm_active=storm,
                    discretionary_threshold_kwh=discretionary_threshold,
                    allow_presolar_discharge=False,
                    bank_socs_pct=planned_banks,
                    bank_capacities_kwh=bank_capacities,
                    controller_settings=controller,
                )
                today_plan_ready = True
                today_ending_bank_socs = today_plan.ending_bank_socs_pct
                await self._capture_daylight_forecast(
                    target_date=today_date,
                    predicted_gain_kwh=today_plan.projected_charge_kwh,
                )
        elif daylight and projection is not None:
            today_plan_ready = True
            today_ending_bank_socs = projection.ending_bank_socs_pct
            if storm:
                today_strategy = STRATEGY_PRESERVE_FOR_RESILIENCE
                today_strategy_reason = "Storm protection is active; preserve stored energy today."
            elif projection.predicted_export_kwh >= discretionary_threshold:
                today_strategy = STRATEGY_USE_DISCRETIONARY_LOADS
                today_strategy_reason = (
                    f"About {projection.predicted_export_kwh:.2f} kWh remains at "
                    "physical export risk; daylight battery discharge is prohibited."
                )
            else:
                today_strategy = STRATEGY_HOLD
                today_strategy_reason = (
                    f"Physical remaining export risk is {projection.predicted_export_kwh:.2f} "
                    "kWh; preserve stored solar."
                )
            await self._start_daylight_sample(
                target_date=today_date,
                stored_kwh=stored,
            )
        elif after_sunset and bank_socs is not None:
            today_plan_ready = True
            today_strategy = STRATEGY_HOLD
            today_strategy_reason = "Today's solar window is complete; preserve stored solar overnight."
            today_ending_bank_socs = bank_socs

        # Flatten today's outputs while retaining the point-forecast baseline.
        if today_plan is not None:
            today_projected_max_soc = today_plan.projected_max_soc_pct
            today_projected_sunset_soc = today_plan.projected_sunset_soc_pct
            today_predicted_export = today_plan.predicted_export_kwh
            today_predicted_grid_import = today_plan.predicted_grid_import_kwh
            today_discretionary = today_plan.discretionary_energy_kwh
            today_model = today_plan.model
            today_capacity_export = today_plan.capacity_limited_export_kwh
            today_power_export = today_plan.power_limited_export_kwh
            today_control_export = today_plan.control_limited_export_kwh
            today_grid_to_battery = today_plan.grid_to_battery_ac_kwh
            today_peak_export = today_plan.peak_export_w
            today_export_minutes = today_plan.export_minutes
        elif daylight and projection is not None:
            today_projected_max_soc = projection.projected_soc
            today_projected_sunset_soc = projection.projected_soc
            today_predicted_export = projection.predicted_export_kwh
            today_predicted_grid_import = projection.predicted_grid_import_kwh
            today_discretionary = (
                projection.predicted_export_kwh
                if projection.predicted_export_kwh >= discretionary_threshold
                else 0.0
            )
            today_model = f"live_{projection.model}"
            today_capacity_export = projection.capacity_limited_export_kwh
            today_power_export = projection.power_limited_export_kwh
            today_control_export = projection.control_limited_export_kwh
            today_grid_to_battery = projection.grid_to_battery_ac_kwh
            today_peak_export = projection.peak_export_w
            today_export_minutes = projection.export_minutes
        elif after_sunset and weighted_soc is not None:
            today_projected_max_soc = weighted_soc
            today_projected_sunset_soc = weighted_soc
            today_predicted_export = 0.0
            today_predicted_grid_import = 0.0
            today_discretionary = 0.0
            today_model = "day_complete"
            today_capacity_export = 0.0
            today_power_export = 0.0
            today_control_export = 0.0
            today_grid_to_battery = 0.0
            today_peak_export = 0.0
            today_export_minutes = 0.0
        else:
            today_projected_max_soc = None
            today_projected_sunset_soc = None
            today_predicted_export = None
            today_predicted_grid_import = None
            today_discretionary = None
            today_model = None
            today_capacity_export = None
            today_power_export = None
            today_control_export = None
            today_grid_to_battery = None
            today_peak_export = None
            today_export_minutes = None

        today_baseline_export = (
            today_nominal.predicted_export_kwh
            if today_nominal is not None
            else today_predicted_export
        )
        today_baseline_capacity_export = (
            today_nominal.capacity_limited_export_kwh
            if today_nominal is not None
            else today_capacity_export
        )

        # Next-day start now includes empirically observed natural overnight
        # depletion. The point forecast uses the median observed rate. The
        # no-regret action test uses the upper empirical rate, because natural
        # depletion creates headroom without intentional battery cycling.
        projected_sunset_banks = today_ending_bank_socs
        if projected_sunset_banks is None and after_sunset:
            projected_sunset_banks = bank_socs

        if after_sunset:
            night_start = now
        else:
            night_start = today_sunset
        night_hours = (
            max((next_day_sunrise - night_start).total_seconds() / 3600.0, 0.0)
            if next_day_sunrise is not None and night_start is not None
            else 0.0
        )

        nominal_overnight_drop = 0.0
        no_regret_overnight_drop = 0.0
        nominal_start_banks = projected_sunset_banks
        conservative_start_banks = projected_sunset_banks
        if projected_sunset_banks is not None:
            sunset_soc = _weighted_soc(projected_sunset_banks, bank_capacities)
            nominal_overnight_drop = projected_overnight_drop_kwh(
                drop_kw=profile.overnight_median_drop_kw,
                night_hours=night_hours,
                sunset_soc_pct=sunset_soc,
                capacity_kwh=capacity,
                reserve_pct=effective_reserve,
            )
            no_regret_overnight_drop = projected_overnight_drop_kwh(
                drop_kw=profile.overnight_upper_drop_kw,
                night_hours=night_hours,
                sunset_soc_pct=sunset_soc,
                capacity_kwh=capacity,
                reserve_pct=effective_reserve,
            )
            nominal_start_banks = apply_stored_energy_drop(
                bank_socs_pct=projected_sunset_banks,
                bank_capacities_kwh=bank_capacities,
                drop_kwh=nominal_overnight_drop,
                reserve_pct=effective_reserve,
            )
            conservative_start_banks = apply_stored_energy_drop(
                bank_socs_pct=projected_sunset_banks,
                bank_capacities_kwh=bank_capacities,
                drop_kwh=no_regret_overnight_drop,
                reserve_pct=effective_reserve,
            )

        projected_next_day_start_soc = (
            _weighted_soc(nominal_start_banks, bank_capacities)
            if nominal_start_banks is not None
            else None
        )

        next_day_plan_ready = False
        next_day_strategy = STRATEGY_INSUFFICIENT_DATA
        next_day_strategy_reason = "Next-day planning inputs are incomplete."
        next_nominal: SolarPeriodPlan | None = None
        next_plan: SolarPeriodPlan | None = None
        next_decision: HeadroomDecision | None = None
        recommended_discharge = 0.0

        if all(
            value is not None
            for value in (
                projected_next_day_start_soc,
                nominal_start_banks,
                conservative_start_banks,
                charge_limit,
                solar_tomorrow,
                base_load_power_w,
                storm_state,
                next_day_sunrise,
                next_day_sunset,
            )
        ):
            next_nominal = plan_solar_period(
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
                discretionary_threshold_kwh=discretionary_threshold,
                allow_presolar_discharge=False,
                bank_socs_pct=nominal_start_banks,
                bank_capacities_kwh=bank_capacities,
                controller_settings=controller,
            )
            conservative_stored = _stored_energy(
                conservative_start_banks,
                bank_capacities,
            )
            charge_ceiling_kwh = capacity * charge_limit / 100.0
            conservative_available = max(charge_ceiling_kwh - conservative_stored, 0.0)
            stored_above_reserve = max(
                conservative_stored - capacity * effective_reserve / 100.0,
                0.0,
            )
            next_decision = confidence_headroom_decision(
                profile=profile,
                nominal_required_headroom_kwh=next_nominal.required_headroom_kwh,
                conservative_available_headroom_kwh=conservative_available,
                stored_above_reserve_kwh=stored_above_reserve,
            )
            next_day_strategy, next_day_strategy_reason = _confidence_strategy(
                nominal=next_nominal,
                decision=next_decision,
                storm=storm,
                controller_enabled=controller.enabled,
                discretionary_threshold_kwh=discretionary_threshold,
                ev_solar_advisory=ev_solar_advisory,
                ev_available=ev_available,
            )
            recommended_discharge = (
                next_decision.recommended_additional_discharge_kwh
                if next_day_strategy == STRATEGY_CREATE_HEADROOM
                else 0.0
            )
            planned_start_banks = apply_stored_energy_drop(
                bank_socs_pct=nominal_start_banks,
                bank_capacities_kwh=bank_capacities,
                drop_kwh=recommended_discharge,
                reserve_pct=effective_reserve,
            )
            planned_start_soc = _weighted_soc(planned_start_banks, bank_capacities)
            next_plan = plan_solar_period(
                sunrise=next_day_sunrise,
                sunset=next_day_sunset,
                current_soc_pct=planned_start_soc,
                capacity_kwh=capacity,
                charge_limit_pct=charge_limit,
                minimum_reserve_pct=effective_reserve,
                solar_forecast_kwh=solar_tomorrow,
                base_load_power_w=base_load_power_w,
                peak_time=peak_time_tomorrow,
                charge_efficiency=charge_efficiency,
                storm_active=storm,
                discretionary_threshold_kwh=discretionary_threshold,
                allow_presolar_discharge=False,
                bank_socs_pct=planned_start_banks,
                bank_capacities_kwh=bank_capacities,
                controller_settings=controller,
            )
            next_day_plan_ready = True
        else:
            planned_start_soc = None

        release = bool(
            auto_headroom
            and next_day_plan_ready
            and profile.action_ready
            and next_day_strategy == STRATEGY_CREATE_HEADROOM
            and recommended_discharge > 0.05
            and not storm
        )

        if after_sunset:
            await self._capture_overnight_start(
                now=now,
                stored_kwh=stored,
                valid_for_calibration=not release and recommended_discharge <= 0.05,
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

        if next_plan is not None and next_nominal is not None and next_decision is not None:
            required_headroom_next = next_nominal.required_headroom_kwh
            nominal_margin_next = next_nominal.headroom_margin_kwh
            nominal_shortfall_next = next_nominal.headroom_shortfall_kwh
            next_baseline_export = next_nominal.predicted_export_kwh
            next_baseline_capacity_export = next_nominal.capacity_limited_export_kwh
            next_baseline_power_export = next_nominal.power_limited_export_kwh
            next_baseline_control_export = next_nominal.control_limited_export_kwh
            next_predicted_export = next_plan.predicted_export_kwh
            next_predicted_import = next_plan.predicted_grid_import_kwh
            next_discretionary = next_plan.discretionary_energy_kwh
            next_capacity_export = next_plan.capacity_limited_export_kwh
            next_power_export = next_plan.power_limited_export_kwh
            next_control_export = next_plan.control_limited_export_kwh
            next_grid_to_battery = next_plan.grid_to_battery_ac_kwh
            next_peak_export = next_plan.peak_export_w
            next_export_minutes = next_plan.export_minutes
            next_projected_max_soc = next_plan.projected_max_soc_pct
            next_projected_sunset_soc = next_plan.projected_sunset_soc_pct
            next_model = next_plan.model
            confidence_required = next_decision.confidence_required_headroom_kwh
            confidence_available = next_decision.conservative_available_headroom_kwh
            confidence_shortfall = next_decision.confidence_shortfall_kwh
        else:
            required_headroom_next = None
            nominal_margin_next = None
            nominal_shortfall_next = None
            next_baseline_export = None
            next_baseline_capacity_export = None
            next_baseline_power_export = None
            next_baseline_control_export = None
            next_predicted_export = None
            next_predicted_import = None
            next_discretionary = None
            next_capacity_export = None
            next_power_export = None
            next_control_export = None
            next_grid_to_battery = None
            next_peak_export = None
            next_export_minutes = None
            next_projected_max_soc = None
            next_projected_sunset_soc = None
            next_model = None
            confidence_required = None
            confidence_available = None
            confidence_shortfall = None

        return {
            "weighted_soc": weighted_soc,
            "stored_energy": stored,
            "battery_headroom": headroom,
            "upcoming_solar": upcoming,
            "reserve": reserve,
            "effective_reserve_floor": effective_reserve,
            "storm": storm_state,
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
            "calibration_status": profile.status,
            "calibration_action_ready": profile.action_ready,
            "calibration_daylight_samples": profile.daylight_samples,
            "calibration_overnight_samples": profile.overnight_samples,
            "calibration_daylight_mae_kwh": profile.daylight_mae_kwh,
            "calibration_daylight_mae_ratio_pct": profile.daylight_mae_ratio * 100.0,
            "calibration_headroom_factor_pct": profile.headroom_factor * 100.0,
            "calibration_overnight_median_kw": profile.overnight_median_drop_kw,
            "calibration_overnight_upper_kw": profile.overnight_upper_drop_kw,
            "today_plan_date": today_date.isoformat(),
            "today_plan_ready": today_plan_ready,
            "today_strategy": today_strategy,
            "today_strategy_reason": today_strategy_reason,
            "today_projected_max_soc": today_projected_max_soc,
            "today_projected_sunset_soc": today_projected_sunset_soc,
            "today_predicted_export": today_predicted_export,
            "today_baseline_export": today_baseline_export,
            "today_baseline_capacity_export": today_baseline_capacity_export,
            "today_predicted_grid_import": today_predicted_grid_import,
            "today_discretionary_energy": today_discretionary,
            "today_recommended_presolar_discharge": today_recommended_discharge,
            "today_projection_model": today_model,
            "today_capacity_limited_export": today_capacity_export,
            "today_power_limited_export": today_power_export,
            "today_control_limited_export": today_control_export,
            "today_grid_to_battery": today_grid_to_battery,
            "today_peak_export_w": today_peak_export,
            "today_export_minutes": today_export_minutes,
            "next_day_plan_date": next_day_date.isoformat(),
            "tomorrow_plan_ready": next_day_plan_ready,
            "tomorrow_strategy": next_day_strategy,
            "tomorrow_strategy_reason": next_day_strategy_reason,
            "required_headroom_tomorrow": required_headroom_next,
            "headroom_margin_tomorrow": nominal_margin_next,
            "headroom_shortfall_tomorrow": nominal_shortfall_next,
            "confidence_required_headroom_tomorrow": confidence_required,
            "confidence_available_headroom_tomorrow": confidence_available,
            "confidence_headroom_shortfall_tomorrow": confidence_shortfall,
            "recommended_overnight_discharge": recommended_discharge,
            "projected_next_day_start_soc": projected_next_day_start_soc,
            "planned_next_day_start_soc": planned_start_soc,
            "nominal_overnight_drop_tomorrow": nominal_overnight_drop,
            "no_regret_overnight_drop_tomorrow": no_regret_overnight_drop,
            "projected_max_soc_tomorrow": next_projected_max_soc,
            "projected_sunset_soc_tomorrow": next_projected_sunset_soc,
            "baseline_export_tomorrow": next_baseline_export,
            "baseline_capacity_export_tomorrow": next_baseline_capacity_export,
            "baseline_power_export_tomorrow": next_baseline_power_export,
            "baseline_control_export_tomorrow": next_baseline_control_export,
            "predicted_export_tomorrow": next_predicted_export,
            "predicted_grid_import_tomorrow": next_predicted_import,
            "discretionary_energy_tomorrow": next_discretionary,
            "tomorrow_projection_model": next_model,
            "next_day_capacity_limited_export": next_capacity_export,
            "next_day_power_limited_export": next_power_export,
            "next_day_control_limited_export": next_control_export,
            "next_day_grid_to_battery": next_grid_to_battery,
            "next_day_peak_export_w": next_peak_export,
            "next_day_export_minutes": next_export_minutes,
        }

    async def async_apply_headroom_policy(self) -> None:
        """Refresh advisory policy state without actuating devices."""
        await self.async_request_refresh()
