"""HVAC shadow learning with live, fail-closed advisory checks."""
import logging

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .hvac import celsius, effective_action, hourly_forecast, number, observe, room_summary
from .hvac_energy import update_energy
from .reliability import suppress_actions
from .v025_coordinator import EnergyPlannerV025Coordinator

_LOGGER = logging.getLogger(__name__)

DEFAULT_ENTITIES = {
    "hvac_thermostat": "climate.thermostat",
    "hvac_condenser": "sensor.hvac_power",
    "hvac_blower": "sensor.ecoflow_smart_home_panel_2_circuit_4_power",
    "hvac_outdoor_temperature": "sensor.ecowitt_outdoor_temperature",
    "hvac_weather": "weather.forecast_home",
    "hvac_stale": "binary_sensor.homepod_indoor_climate_stale_readings",
}
ROOM_PREFIXES = (
    "sensor.homepod_indoor_climate_living_room",
    "sensor.homepod_indoor_climate_guest_bedroom",
    "sensor.homepod_indoor_climate_main_bedroom_left",
    "sensor.homepod_indoor_climate_main_bedroom_right",
)
_LEGACY_HOMEPOD_PREFIX = "sensor.home_homepod_indoor_climate_"
_CANONICAL_HOMEPOD_PREFIX = "sensor.homepod_indoor_climate_"
WEATHER_REFRESH_SECONDS = 1800
WEATHER_RETRY_SECONDS = 300
WEATHER_CACHE_MAX_AGE_SECONDS = 6 * 3600



class EnergyPlannerHVACCoordinator(EnergyPlannerV025Coordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._hvac_store = Store(hass, 1, f"energy_planner.{entry.entry_id}.hvac")
        self._hvac_memory = None
        self._hvac_persistence = {
            "status": "not_loaded",
            "restored_samples": 0,
            "restored_recovery_cycles": 0,
            "restored_thermal_samples": 0,
            "reset_reason": None,
        }
        self._hvac_energy_store = Store(
            hass, 1, f"energy_planner.{entry.entry_id}.hvac_energy"
        )
        self._hvac_energy = None
        self._weather_hours = []
        self._weather_at = None

    def _entity(self, key):
        return self.cfg.get(key, DEFAULT_ENTITIES[key])

    def _room_prefixes(self):
        """Resolve configured room prefixes against the entities HA actually has."""
        raw = self.cfg.get("hvac_room_prefixes", ",".join(ROOM_PREFIXES))
        prefixes = []
        for value in str(raw).split(","):
            prefix = value.strip()
            if not prefix:
                continue

            candidates = [prefix]
            if prefix.startswith(_LEGACY_HOMEPOD_PREFIX):
                candidates.append(
                    prefix.replace(
                        _LEGACY_HOMEPOD_PREFIX,
                        _CANONICAL_HOMEPOD_PREFIX,
                        1,
                    )
                )
            elif prefix.startswith(_CANONICAL_HOMEPOD_PREFIX):
                candidates.append(
                    prefix.replace(
                        _CANONICAL_HOMEPOD_PREFIX,
                        _LEGACY_HOMEPOD_PREFIX,
                        1,
                    )
                )

            def score(candidate):
                return sum(
                    self.hass.states.get(candidate + suffix) is not None
                    for suffix in ("_temperature", "_humidity")
                )

            best = max(candidates, key=score)
            prefixes.append(best if score(best) > 0 else prefix)
        return tuple(prefixes)

    @staticmethod
    def _canonical_room_prefix(prefix):
        """Treat legacy/current HomePod entity prefixes as one logical source."""
        prefix = str(prefix or "").strip()
        if prefix.startswith(_LEGACY_HOMEPOD_PREFIX):
            return prefix.replace(
                _LEGACY_HOMEPOD_PREFIX,
                _CANONICAL_HOMEPOD_PREFIX,
                1,
            )
        return prefix

    def _model_identity(self, prefixes):
        """Return a stable identity for data that materially affects learning."""
        identity = {k: self._entity(k) for k in DEFAULT_ENTITIES}
        identity["room_prefixes"] = ",".join(
            self._canonical_room_prefix(prefix) for prefix in prefixes
        )
        return identity

    def _normalize_stored_identity(self, identity):
        """Normalize historical identity records before comparing them."""
        if not isinstance(identity, dict):
            return None
        normalized = dict(identity)
        raw = normalized.get("room_prefixes", "")
        values = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
        normalized["room_prefixes"] = ",".join(
            self._canonical_room_prefix(value)
            for value in values
            if str(value).strip()
        )
        return normalized

    @staticmethod
    def _clear_restart_continuity(memory):
        """Discard only state that falsely implies continuity across a reboot."""
        for key in (
            "previous",
            "call",
            "missing_since",
            "recovery_call",
            "thermal_window",
        ):
            memory.pop(key, None)

    def _restore_hvac_memory(self, loaded, identity):
        """Restore learned history without alias-only startup resets."""
        memory = loaded if isinstance(loaded, dict) else {}
        stored_identity = self._normalize_stored_identity(
            memory.get("entity_mapping")
        )
        current_identity = self._normalize_stored_identity(identity)

        weather_cache = memory.get("weather_cache") if isinstance(memory.get("weather_cache"), dict) else {}
        weather_hours = weather_cache.get("hours") if isinstance(weather_cache.get("hours"), list) else []
        restored_recovery_call = "recovery_call" in memory
        restored_thermal_window = "thermal_window" in memory

        restored = {
            "samples": len(memory.get("samples", [])),
            "recovery_cycles": len(memory.get("recovery_cycles", [])),
            "thermal_samples": len(memory.get("thermal_samples", [])),
            "weather_hours": len(weather_hours),
        }

        # Older stores may not have an identity marker. Adopt the current
        # identity rather than destroying otherwise valid learned history.
        if stored_identity is None:
            memory["entity_mapping"] = identity
            status = "restored_identity_adopted"
            reset_reason = None
        elif stored_identity == current_identity:
            # Write back the normalized identity so legacy/canonical HomePod
            # aliases converge permanently after this successful load.
            memory["entity_mapping"] = identity
            status = "restored"
            reset_reason = None
        else:
            # A real source mapping change can make historical power/temperature
            # relationships incompatible. Reset only for this material change.
            memory = {"entity_mapping": identity}
            status = "reset_mapping_changed"
            reset_reason = {
                "stored": stored_identity,
                "current": current_identity,
            }

        if reset_reason is None:
            # Only genuine persisted continuity gets startup grace. An empty
            # store/cold start must still surface missing inputs immediately.
            previous_state = memory.get("previous")
            restart_candidate = (
                isinstance(previous_state, dict)
                and any(
                    isinstance(memory.get(key), dict)
                    for key in (
                        "call",
                        "recovery_call",
                        "recovery_eta",
                        "thermal_window",
                    )
                )
            )
            if restart_candidate:
                # The first complete live observation validates
                # mode/action/target before continuing persisted state.
                memory["_restart_pending"] = True
        else:
            self._clear_restart_continuity(memory)
        self._hvac_persistence = {
            "status": status,
            "restored_samples": restored["samples"] if reset_reason is None else 0,
            "restored_recovery_cycles": (
                restored["recovery_cycles"] if reset_reason is None else 0
            ),
            "restored_thermal_samples": (
                restored["thermal_samples"] if reset_reason is None else 0
            ),
            "restored_weather_hours": (
                restored["weather_hours"] if reset_reason is None else 0
            ),
            "interrupted_recovery_call": False,
            "interrupted_thermal_window": False,
            "restored_recovery_call": (
                restored_recovery_call if reset_reason is None else False
            ),
            "restored_thermal_window": (
                restored_thermal_window if reset_reason is None else False
            ),
            "restart_resume_status": (
                "pending_validation"
                if reset_reason is None and memory.get("_restart_pending")
                else "not_applicable"
            ),
            "restart_gap_minutes": None,
            "rate_learning_rebased": False,
            "reset_reason": reset_reason,
        }
        return memory

    def _fresh_state(self, entity, now, *, room=False):
        state = self.hass.states.get(entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        if room:
            stamp = dt_util.parse_datetime(state.attributes.get("last_received", ""))
            if not state.attributes.get("fresh"):
                return None
            if stamp is None or not 0 <= (now - stamp).total_seconds() <= 900:
                return None
        # HA climate, weather and numeric outdoor states can remain unchanged for
        # hours. Their report timestamp is not a validity deadline. HomePod room
        # entities carry an explicit freshness signal and receive time above.
        return state

    def _input_state_details(self, entity, now):
        state = self.hass.states.get(entity)
        if state is None:
            return {"entity_id": entity, "raw_state": "missing", "available": False,
                    "reason": "entity_missing"}
        stamp = getattr(state, "last_reported", state.last_updated)
        return {"entity_id": entity, "raw_state": state.state,
                "available": state.state not in ("unavailable", "unknown"),
                "reason": "ha_state_available" if state.state not in ("unavailable", "unknown") else "ha_state_unavailable",
                "last_reported": stamp.isoformat() if stamp else None,
                "report_age_minutes": round((now - stamp).total_seconds() / 60, 1) if stamp else None,
                "current_temperature": state.attributes.get("current_temperature"),
                "target_temperature": state.attributes.get("temperature"),
                "current_humidity": state.attributes.get("current_humidity")}

    def _available_power(self, entity):
        """Read a numeric HA power state without requiring a recent state change.

        A stable numeric state such as 0 W remains valid until Home Assistant marks
        the entity unknown/unavailable. Integration refresh age is useful as
        diagnostics, but it is not a validity requirement for these circuit states.
        """
        state = self.hass.states.get(entity)
        diagnostics = {
            "entity_id": entity,
            "available": False,
            "raw_state": "missing",
            "unit": None,
            "last_reported": None,
            "reason": "entity_missing",
        }
        if state is None:
            return None, diagnostics

        stamp = getattr(state, "last_reported", state.last_updated)
        diagnostics.update(
            raw_state=state.state,
            unit=state.attributes.get("unit_of_measurement"),
            last_reported=stamp.isoformat() if stamp is not None else None,
        )
        if state.state in ("unavailable", "unknown", "none", ""):
            diagnostics["reason"] = "state_unavailable"
            return None, diagnostics

        value = number(state.state)
        unit = diagnostics["unit"]
        if value is None:
            diagnostics["reason"] = "state_not_numeric"
            return None, diagnostics
        if unit == "kW":
            value *= 1000
        elif unit != "W":
            diagnostics["reason"] = "unsupported_unit"
            return None, diagnostics
        if value < 0:
            diagnostics["reason"] = "negative_power"
            return None, diagnostics

        diagnostics.update(available=True, reason="numeric_available", power_w=value)
        return value, diagnostics

    @staticmethod
    def _usable_weather_hours(cache, now):
        """Return only a recent last-known-good forecast after restart/fetch failures."""
        if not isinstance(cache, dict):
            return []
        last_success = number(cache.get("last_success"))
        now_ts = now.timestamp()
        if (
            last_success is None
            or now_ts < last_success
            or now_ts - last_success > WEATHER_CACHE_MAX_AGE_SECONDS
        ):
            return []
        rows = cache.get("hours")
        if not isinstance(rows, list):
            return []
        usable = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            at = number(row.get("at"))
            if at is None or not now_ts <= at <= now_ts + 7 * 86400:
                continue
            usable.append(row)
        return usable

    def _weather_diagnostics(self, cache, now, source):
        now_ts = now.timestamp()
        last_success = number(cache.get("last_success")) if isinstance(cache, dict) else None
        last_attempt = number(cache.get("last_attempt")) if isinstance(cache, dict) else None
        age_minutes = (
            round((now_ts - last_success) / 60, 1)
            if last_success is not None and now_ts >= last_success
            else None
        )
        rows = cache.get("hours") if isinstance(cache, dict) else []
        return {
            "source": source,
            "last_success": last_success,
            "last_attempt": last_attempt,
            "last_error": cache.get("last_error") if isinstance(cache, dict) else None,
            "last_error_at": cache.get("last_error_at") if isinstance(cache, dict) else None,
            "cache_age_minutes": age_minutes,
            "cached_hours": len(rows) if isinstance(rows, list) else 0,
            "max_cache_age_minutes": WEATHER_CACHE_MAX_AGE_SECONDS / 60,
            "retry_minutes": WEATHER_RETRY_SECONDS / 60,
        }

    async def _hourly_weather(self, now):
        """Refresh hourly weather without destroying the last known good forecast."""
        if self._hvac_memory is None:
            return [], self._weather_diagnostics({}, now, "unavailable")
        cache = self._hvac_memory.get("weather_cache")
        if not isinstance(cache, dict):
            cache = {}
            self._hvac_memory["weather_cache"] = cache

        now_ts = now.timestamp()
        last_attempt = number(cache.get("last_attempt"))
        retry_after = (
            WEATHER_RETRY_SECONDS if cache.get("last_error") else WEATHER_REFRESH_SECONDS
        )
        if (
            last_attempt is not None
            and 0 <= now_ts - last_attempt < retry_after
        ):
            hours = self._usable_weather_hours(cache, now)
            source = (
                "cached_fallback"
                if cache.get("last_error") and hours
                else "cached"
                if hours
                else "unavailable"
            )
            return hours, self._weather_diagnostics(cache, now, source)

        cache["last_attempt"] = now_ts
        entity = self._entity("hvac_weather")
        state = self.hass.states.get(entity)
        refreshed = False
        if state is None or state.state in ("unknown", "unavailable"):
            cache["last_error"] = "weather_entity_unavailable"
            cache["last_error_at"] = now_ts
        else:
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": entity, "type": "hourly"},
                    blocking=True,
                    return_response=True,
                )
                candidate = []
                for row in (response or {}).get(entity, {}).get("forecast", []):
                    at = dt_util.parse_datetime(row.get("datetime", ""))
                    if at is not None and at.tzinfo is not None:
                        candidate.append(
                            {
                                "at": at.timestamp(),
                                "temperature_c": celsius(
                                    row.get("temperature"),
                                    state.attributes.get("temperature_unit"),
                                ),
                                "humidity": number(row.get("humidity")),
                            }
                        )
                if candidate:
                    cache["hours"] = candidate
                    cache["last_success"] = now_ts
                    cache["last_error"] = None
                    cache["last_error_at"] = None
                    refreshed = True
                else:
                    cache["last_error"] = "empty_hourly_forecast"
                    cache["last_error_at"] = now_ts
            except Exception as err:
                cache["last_error"] = f"weather_service_error:{type(err).__name__}"
                cache["last_error_at"] = now_ts
                _LOGGER.warning(
                    "HVAC hourly weather refresh failed; retaining last known good forecast",
                    exc_info=True,
                )

        hours = self._usable_weather_hours(cache, now)
        source = "live_refresh" if refreshed else "cached_fallback" if hours else "unavailable"
        # Weather is restart-sensitive and cheap to persist. Save immediately so a
        # second HA restart cannot erase a successful refresh before the main cycle ends.
        await self._hvac_store.async_save(self._hvac_memory)
        return hours, self._weather_diagnostics(cache, now, source)

    async def _async_update_data(self):
        data = await super()._async_update_data()
        now = dt_util.now()

        condenser_w = None
        blower_w = None
        power_sources = {}
        power_mapping_valid = False
        try:
            if self._hvac_energy is None:
                self._hvac_energy = await self._hvac_energy_store.async_load() or {}

            mapping = {
                "condenser": self._entity("hvac_condenser"),
                "blower_controls": self._entity("hvac_blower"),
            }
            mapping_list = [mapping["condenser"], mapping["blower_controls"]]
            if self._hvac_energy.get("mapping") != mapping_list:
                self._hvac_energy = {}

            condenser_w, power_sources["condenser"] = self._available_power(
                mapping["condenser"]
            )
            blower_w, power_sources["blower_controls"] = self._available_power(
                mapping["blower_controls"]
            )
            power_mapping_valid = len(set(mapping_list)) == 2
            if not power_mapping_valid:
                for details in power_sources.values():
                    details["available"] = False
                    details["reason"] = "duplicate_mapping"
                condenser_w = None
                blower_w = None

            power = (
                condenser_w + blower_w
                if condenser_w is not None and blower_w is not None
                else None
            )
            coverage = update_energy(self._hvac_energy, now, power)
            self._hvac_energy["mapping"] = mapping_list
            data.update(
                hvac_electrical_power_w=power,
                hvac_daily_electricity_kwh=self._hvac_energy["kwh"],
                hvac_energy_coverage=coverage,
                hvac_power_sources=power_sources,
                hvac_power_mapping_valid=power_mapping_valid,
            )
            await self._hvac_energy_store.async_save(self._hvac_energy)
        except Exception:
            _LOGGER.exception("HVAC electricity tracking unavailable")
            data.update(
                hvac_electrical_power_w=None,
                hvac_daily_electricity_kwh=None,
                hvac_power_sources=power_sources,
                hvac_power_mapping_valid=power_mapping_valid,
            )

        if not self.cfg.get("hvac_learning_enabled", False):
            data.update(
                hvac_status="disabled",
                hvac_diagnostics={
                    "mode": "disabled",
                    "power_sources": power_sources,
                    "power_mapping_valid": power_mapping_valid,
                },
            )
            return data

        try:
            prefixes = self._room_prefixes()
            identity = self._model_identity(prefixes)

            if self._hvac_memory is None:
                loaded = await self._hvac_store.async_load() or {}
                self._hvac_memory = self._restore_hvac_memory(loaded, identity)
                self._weather_at = None
            elif (
                self._normalize_stored_identity(
                    self._hvac_memory.get("entity_mapping")
                )
                != self._normalize_stored_identity(identity)
            ):
                # Runtime option changes are different from alias-only startup
                # resolution. Reset learned relationships only when the actual
                # configured source mapping materially changes.
                previous_identity = self._normalize_stored_identity(
                    self._hvac_memory.get("entity_mapping")
                )
                self._hvac_memory = {"entity_mapping": identity}
                self._hvac_persistence = {
                    "status": "reset_mapping_changed",
                    "restored_samples": 0,
                    "restored_recovery_cycles": 0,
                    "restored_thermal_samples": 0,
                    "restored_weather_hours": 0,
                    "interrupted_recovery_call": False,
                    "interrupted_thermal_window": False,
                    "restored_recovery_call": False,
                    "restored_thermal_window": False,
                    "restart_resume_status": "not_applicable",
                    "restart_gap_minutes": None,
                    "rate_learning_rebased": False,
                    "reset_reason": {
                        "stored": previous_identity,
                        "current": self._normalize_stored_identity(identity),
                    },
                }
                self._weather_at = None
            else:
                self._hvac_memory["entity_mapping"] = identity

            thermostat = self._fresh_state(self._entity("hvac_thermostat"), now)
            attrs = thermostat.attributes if thermostat else {}
            temp_unit = attrs.get(
                "temperature_unit", self.hass.config.units.temperature_unit
            )
            outdoor = self._fresh_state(
                self._entity("hvac_outdoor_temperature"), now
            )

            temperatures, humidities = [], []
            room_entities = []
            for prefix in prefixes:
                temp_id = prefix + "_temperature"
                humidity_id = prefix + "_humidity"
                temp = self._fresh_state(temp_id, now, room=True)
                hum = self._fresh_state(humidity_id, now, room=True)
                temperatures.append(
                    celsius(
                        temp.state, temp.attributes.get("unit_of_measurement")
                    )
                    if temp
                    else None
                )
                value = number(hum.state) if hum else None
                humidities.append(
                    value if value is not None and 0 <= value <= 100 else None
                )
                room_entities.append(
                    {
                        "prefix": prefix,
                        "temperature": temp_id,
                        "humidity": humidity_id,
                        "available": temp is not None and hum is not None,
                    }
                )

            rooms = (
                room_summary(temperatures, humidities)
                if len(prefixes) == 4
                else {"room_count": 0}
            )
            stale = self.hass.states.get(self._entity("hvac_stale"))
            room_health = (
                stale is not None
                and stale.state == "off"
                and len(temperatures) == 4
                and all(v is not None for v in temperatures + humidities)
            )
            weather = self._fresh_state(self._entity("hvac_weather"), now)
            reported_action = attrs.get("hvac_action")
            action, action_source = effective_action(
                thermostat.state if thermostat else None,
                reported_action,
                condenser_w,
                blower_w,
            )
            precision_indoor_c = (
                rooms.get("precision_temperature_c")
                if room_health
                else None
            )
            sample = {
                "at": now.timestamp(),
                "day": now.date().isoformat(),
                "mode": thermostat.state if thermostat else None,
                "action": action,
                "target_c": celsius(attrs.get("temperature"), temp_unit),
                "indoor_c": celsius(attrs.get("current_temperature"), temp_unit),
                "precision_indoor_c": precision_indoor_c,
                "precision_temperature_source": (
                    "homepod_physical_room_median"
                    if precision_indoor_c is not None
                    else None
                ),
                "humidity": number(attrs.get("current_humidity")),
                "outdoor_c": (
                    celsius(
                        outdoor.state,
                        outdoor.attributes.get("unit_of_measurement"),
                    )
                    if outdoor
                    else None
                ),
                "outdoor_humidity": (
                    number(weather.attributes.get("humidity")) if weather else None
                ),
                "condenser_w": condenser_w,
                "blower_w": blower_w,
            }

            # HomePods are a precision enhancement, not a hard dependency.
            # _temperature_signal falls back to the thermostat when room data is
            # stale/unavailable.

            required_sample_values = (
                "indoor_c",
                "target_c",
                "outdoor_c",
                "condenser_w",
                "blower_w",
                "humidity",
            )
            restart_pending = bool(self._hvac_memory.get("_restart_pending"))
            startup_inputs_ready = (
                action in ("cooling", "heating", "idle", "off", "fan")
                and all(number(sample.get(key)) is not None for key in required_sample_values)
            )
            restart_waiting = restart_pending and not startup_inputs_ready

            if restart_waiting:
                # Do not let integrations/entities that restore a minute later than
                # Energy Planner erase a persisted active call. Keep the state
                # pending until a complete live observation can validate it.
                rows = self._hvac_memory.get("samples", [])
                days = len(
                    {
                        row.get("day")
                        for row in rows
                        if isinstance(row, dict) and row.get("day")
                    }
                )
                learning_ready = len(rows) >= 36 and days >= 3
                recovery_call = self._hvac_memory.get("recovery_call")
                recovery_eta = self._hvac_memory.get("recovery_eta")
                eta_target_at = (
                    number(recovery_eta.get("target_at"))
                    if isinstance(recovery_eta, dict)
                    else None
                )
                eta_minutes = (
                    max(0.0, min(360.0, (eta_target_at - now.timestamp()) / 60))
                    if eta_target_at is not None
                    else None
                )
                call_at = (
                    number(recovery_call.get("at"))
                    if isinstance(recovery_call, dict)
                    else None
                )
                call_minutes = (
                    max(0.0, (now.timestamp() - call_at) / 60)
                    if call_at is not None
                    else None
                )
                previous_state = self._hvac_memory.get("previous")
                previous_at = (
                    number(previous_state.get("at"))
                    if isinstance(previous_state, dict)
                    else None
                )
                thermal_window = self._hvac_memory.get("thermal_window")
                restart_resume = {
                    "status": "pending_live_inputs",
                    "gap_minutes": (
                        max(0.0, (now.timestamp() - previous_at) / 60)
                        if previous_at is not None
                        else None
                    ),
                    "resumed_recovery_call": bool(recovery_call),
                    "resumed_thermal_window": isinstance(thermal_window, dict),
                    "rate_learning_rebased": False,
                }
                self._hvac_persistence.update(
                    restart_resume_status="pending_live_inputs",
                    restart_gap_minutes=restart_resume["gap_minutes"],
                    restored_recovery_call=bool(recovery_call),
                    restored_thermal_window=isinstance(
                        thermal_window, dict
                    ),
                    rate_learning_rebased=False,
                    interrupted_recovery_call=False,
                    interrupted_thermal_window=False,
                )
                diagnostics = {
                    "status": "ready" if learning_ready else "learning",
                    "reason": (
                        "Waiting for live HVAC inputs after restart; persisted "
                        "learning and active-cycle state retained"
                    ),
                    "samples": len(rows),
                    "learning_ready": learning_ready,
                    "learning_samples": len(rows),
                    "learning_samples_required": 36,
                    "learning_days": days,
                    "learning_days_required": 3,
                    "learning_progress_pct": round(
                        100 * min(len(rows) / 36, days / 3, 1.0), 1
                    ),
                    "electrical_power_w": (
                        condenser_w + blower_w
                        if condenser_w is not None and blower_w is not None
                        else None
                    ),
                    "thermostat_action": action,
                    "unmet_setpoint": None,
                    "restart_resume": restart_resume,
                    "recovery": {
                        "active": bool(recovery_call),
                        "demand_active": None,
                        "action": (
                            recovery_call.get("action")
                            if isinstance(recovery_call, dict)
                            else None
                        ),
                        "requested_action": (
                            recovery_call.get("action")
                            if isinstance(recovery_call, dict)
                            else None
                        ),
                        "status": "restoring",
                        "confidence": "unknown",
                        "eta_minutes": eta_minutes,
                        "eta_raw_minutes": None,
                        "eta_target_at": eta_target_at,
                        "eta_method": (
                            "target_time_countdown"
                            if eta_target_at is not None
                            else None
                        ),
                        "eta_last_corrected_at": (
                            recovery_eta.get("last_corrected_at")
                            if isinstance(recovery_eta, dict)
                            else None
                        ),
                        "eta_correction_reason": "restart_resume_pending",
                        "rate_c_per_hour": None,
                        "source": "restart_resume_pending",
                        "call_minutes": call_minutes,
                        "rate_segment_minutes": None,
                        "completed_cycles": len(
                            self._hvac_memory.get("recovery_cycles", [])
                        ),
                        "matched_cycles": 0,
                        "models": {},
                        "restart_interrupted_cycles": int(
                            self._hvac_memory.get(
                                "recovery_restart_interruptions", 0
                            )
                        ),
                    },
                    "thermal": {
                        "status": "restoring",
                        "confidence": "unknown",
                        "source": "restart_resume_pending",
                        "samples": len(
                            self._hvac_memory.get("thermal_samples", [])
                        ),
                        "live_window_minutes": (
                            max(
                                0.0,
                                (
                                    number(thermal_window.get("last_at"))
                                    - number(thermal_window.get("at"))
                                )
                                / 60,
                            )
                            if isinstance(thermal_window, dict)
                            and number(thermal_window.get("last_at")) is not None
                            and number(thermal_window.get("at")) is not None
                            else 0
                        ),
                    },
                }
            else:
                diagnostics = observe(self._hvac_memory, sample)
                restart_resume = diagnostics.get("restart_resume")
                if isinstance(restart_resume, dict):
                    self._hvac_persistence.update(
                        restart_resume_status=restart_resume.get("status"),
                        restart_gap_minutes=restart_resume.get("gap_minutes"),
                        restored_recovery_call=restart_resume.get(
                            "resumed_recovery_call", False
                        ),
                        restored_thermal_window=restart_resume.get(
                            "resumed_thermal_window", False
                        ),
                        rate_learning_rebased=restart_resume.get(
                            "rate_learning_rebased", False
                        ),
                        interrupted_recovery_call=False,
                        interrupted_thermal_window=False,
                    )
            hours, weather_diagnostics = await self._hourly_weather(now)
            shadow = hourly_forecast(
                self._hvac_memory.get("samples", []),
                hours,
                mode=sample["mode"],
                target_c=sample["target_c"],
                now=now.timestamp(),
            )
            supported_hours = sum(
                row.get("status") == "shadow" for row in shadow
            )
            diagnostics.update(
                rooms=rooms,
                room_data_healthy=room_health,
                room_entities=room_entities,
                room_prefixes=list(prefixes),
                thermostat_mode=thermostat.state if thermostat else None,
                thermostat_action_reported=reported_action,
                thermostat_action_effective=action,
                thermostat_action_source=action_source,
                hourly_shadow=shadow,
                hourly_supported_hours=supported_hours,
                hourly_forecast_hours=len(shadow),
                forecast_applied=False,
                thermostat_control_enabled=False,
                weather_available=bool(hours),
                weather_forecast=weather_diagnostics,
                entities={k: self._entity(k) for k in DEFAULT_ENTITIES},
                input_states={key: self._input_state_details(self._entity(key), now)
                              for key in ("hvac_thermostat", "hvac_outdoor_temperature", "hvac_weather", "hvac_stale")},
                power_sources=power_sources,
                power_mapping_valid=power_mapping_valid,
                persistence=dict(getattr(self, "_hvac_persistence", {})),
            )
            if not room_health:
                diagnostics["temperature_precision_status"] = "thermostat_fallback"
            else:
                diagnostics["temperature_precision_status"] = "homepod_precision"

            data.update(
                hvac_status=diagnostics["status"],
                hvac_diagnostics=diagnostics,
            )
            await self._hvac_store.async_save(self._hvac_memory)

            if diagnostics["status"] in ("suspected_fault", "unavailable"):
                reason = (
                    diagnostics["reason"]
                    + "—do not act on discretionary forecast advice."
                )
                suppress_actions(data, "hvac_hold", reason)
                data.update(
                    forecast_reliability_status="hvac_hold",
                    forecast_reliability_reason=reason,
                    forecast_confidence="low",
                )
                self._surplus_since = None
        except Exception:
            _LOGGER.exception(
                "HVAC evaluation failed; withholding discretionary advice"
            )
            reason = "HVAC evaluation unavailable—do not act."
            suppress_actions(data, "hvac_hold", reason)
            data.update(
                hvac_status="unavailable",
                hvac_diagnostics={"reason": reason},
                forecast_reliability_status="hvac_hold",
                forecast_reliability_reason=reason,
                forecast_confidence="low",
            )
            self._surplus_since = None
        return data
