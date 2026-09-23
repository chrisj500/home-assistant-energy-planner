"""HVAC shadow learning with live, fail-closed advisory checks."""
import logging

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .hvac import celsius, hourly_forecast, number, observe, room_summary
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
    "sensor.home_homepod_indoor_climate_guest_bedroom",
    "sensor.home_homepod_indoor_climate_main_bedroom_left",
    "sensor.home_homepod_indoor_climate_main_bedroom_right",
)


class EnergyPlannerHVACCoordinator(EnergyPlannerV025Coordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._hvac_store = Store(hass, 1, f"energy_planner.{entry.entry_id}.hvac")
        self._hvac_memory = None
        self._weather_hours = []
        self._weather_at = None

    def _entity(self, key):
        return self.cfg.get(key, DEFAULT_ENTITIES[key])

    def _fresh_state(self, entity, now, *, room=False):
        state = self.hass.states.get(entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        stamp = getattr(state, "last_reported", state.last_updated)
        if room:
            stamp = dt_util.parse_datetime(state.attributes.get("last_received", ""))
            if not state.attributes.get("fresh"):
                return None
        if stamp is None or not 0 <= (now - stamp).total_seconds() <= 900:
            return None
        return state

    async def _hourly_weather(self, now):
        if self._weather_at is not None and (now - self._weather_at).total_seconds() < 1800:
            return self._weather_hours
        self._weather_at = now
        self._weather_hours = []
        entity = self._entity("hvac_weather")
        state = self.hass.states.get(entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return []
        try:
            response = await self.hass.services.async_call(
                "weather", "get_forecasts", {"entity_id": entity, "type": "hourly"},
                blocking=True, return_response=True)
            for row in (response or {}).get(entity, {}).get("forecast", []):
                at = dt_util.parse_datetime(row.get("datetime", ""))
                if at is not None and at.tzinfo is not None:
                    self._weather_hours.append({"at": at.timestamp(),
                        "temperature_c": celsius(row.get("temperature"), state.attributes.get("temperature_unit")),
                        "humidity": number(row.get("humidity"))})
        except Exception:
            _LOGGER.warning("HVAC hourly weather unavailable; retaining baseline forecast", exc_info=True)
        return self._weather_hours

    async def _async_update_data(self):
        data = await super()._async_update_data()
        if not self.cfg.get("hvac_learning_enabled", False):
            data.update(hvac_status="disabled", hvac_diagnostics={"mode": "disabled"})
            return data
        now = dt_util.now()
        try:
            if self._hvac_memory is None:
                self._hvac_memory = await self._hvac_store.async_load() or {}
                # A restart is a gap, never evidence of continuous unmet demand.
                self._hvac_memory.pop("previous", None)
            identity = {k: self._entity(k) for k in DEFAULT_ENTITIES}
            identity["room_prefixes"] = self.cfg.get("hvac_room_prefixes", ",".join(ROOM_PREFIXES))
            if self._hvac_memory.get("entity_mapping") != identity:
                self._hvac_memory = {"entity_mapping": identity}
                self._weather_at = None
            thermostat = self._fresh_state(self._entity("hvac_thermostat"), now)
            attrs = thermostat.attributes if thermostat else {}
            temp_unit = attrs.get("temperature_unit", self.hass.config.units.temperature_unit)
            outdoor = self._fresh_state(self._entity("hvac_outdoor_temperature"), now)
            prefixes = tuple(self.cfg.get("hvac_room_prefixes", ",".join(ROOM_PREFIXES)).split(","))
            temperatures, humidities = [], []
            for prefix in prefixes:
                temp = self._fresh_state(prefix.strip() + "_temperature", now, room=True)
                hum = self._fresh_state(prefix.strip() + "_humidity", now, room=True)
                temperatures.append(celsius(temp.state, temp.attributes.get("unit_of_measurement")) if temp else None)
                value = number(hum.state) if hum else None
                humidities.append(value if value is not None and 0 <= value <= 100 else None)
            rooms = room_summary(temperatures, humidities) if len(prefixes) == 4 else {"room_count": 0}
            stale = self.hass.states.get(self._entity("hvac_stale"))
            room_health = (stale is not None and stale.state == "off" and
                           len(temperatures) == 4 and all(v is not None for v in temperatures + humidities))
            weather = self._fresh_state(self._entity("hvac_weather"), now)
            sample = {"at": now.timestamp(), "mode": thermostat.state if thermostat else None,
                "action": attrs.get("hvac_action"), "target_c": celsius(attrs.get("temperature"), temp_unit),
                "indoor_c": celsius(attrs.get("current_temperature"), temp_unit),
                "humidity": number(attrs.get("current_humidity")),
                "outdoor_c": celsius(outdoor.state, outdoor.attributes.get("unit_of_measurement")) if outdoor else None,
                "outdoor_humidity": number(weather.attributes.get("humidity")) if weather else None,
                "condenser_w": self._fresh_power(self._entity("hvac_condenser"), now),
                "blower_w": self._fresh_power(self._entity("hvac_blower"), now)}
            for key in ("condenser_w", "blower_w"):
                if sample[key] is not None and sample[key] < 0:
                    sample[key] = None
            # Never train through a room telemetry outage.
            if not room_health:
                sample["humidity"] = None
            diagnostics = observe(self._hvac_memory, sample)
            hours = await self._hourly_weather(now)
            shadow = hourly_forecast(self._hvac_memory.get("samples", []), hours,
                mode=sample["mode"], target_c=sample["target_c"], now=now.timestamp())
            diagnostics.update(rooms=rooms, room_data_healthy=room_health,
                hourly_shadow=shadow, forecast_applied=False, thermostat_control_enabled=False,
                weather_available=bool(hours), entities={k: self._entity(k) for k in DEFAULT_ENTITIES})
            if not room_health:
                diagnostics.update(status="unavailable", reason="Indoor room data missing or stale")
            data.update(hvac_status=diagnostics["status"], hvac_diagnostics=diagnostics,
                        hvac_electrical_power_w=diagnostics.get("electrical_power_w"))
            await self._hvac_store.async_save(self._hvac_memory)
            if diagnostics["status"] in ("suspected_fault", "unavailable"):
                reason = diagnostics["reason"] + "—do not act on discretionary forecast advice."
                suppress_actions(data, "hvac_hold", reason)
                data.update(forecast_reliability_status="hvac_hold", forecast_reliability_reason=reason,
                            forecast_confidence="low")
                self._surplus_since = None
        except Exception:
            _LOGGER.exception("HVAC evaluation failed; withholding discretionary advice")
            reason = "HVAC evaluation unavailable—do not act."
            suppress_actions(data, "hvac_hold", reason)
            data.update(hvac_status="unavailable", hvac_diagnostics={"reason": reason},
                        forecast_reliability_status="hvac_hold", forecast_reliability_reason=reason,
                        forecast_confidence="low")
            self._surplus_since = None
        return data
