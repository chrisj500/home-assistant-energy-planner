"""HVAC shadow learning with live, fail-closed advisory checks."""
import logging

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .hvac import celsius, hourly_forecast, number, observe, room_summary
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


class EnergyPlannerHVACCoordinator(EnergyPlannerV025Coordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._hvac_store = Store(hass, 1, f"energy_planner.{entry.entry_id}.hvac")
        self._hvac_memory = None
        self._hvac_energy_store = Store(
            hass, 1, f"energy_planner.{entry.entry_id}.hvac_energy"
        )
        self._hvac_energy = None
        self._weather_hours = []
        self._weather_at = None

    def _entity(self, key):
        return self.cfg.get(key, DEFAULT_ENTITIES[key])

    def _room_prefixes(self):
        """Resolve configured room prefixes and repair the v0.1.29 typo safely."""
        raw = self.cfg.get("hvac_room_prefixes", ",".join(ROOM_PREFIXES))
        prefixes = []
        for value in str(raw).split(","):
            prefix = value.strip()
            if not prefix:
                continue
            if prefix.startswith(_LEGACY_HOMEPOD_PREFIX):
                corrected = prefix.replace(
                    _LEGACY_HOMEPOD_PREFIX, _CANONICAL_HOMEPOD_PREFIX, 1
                )
                old_exists = any(
                    self.hass.states.get(prefix + suffix) is not None
                    for suffix in ("_temperature", "_humidity")
                )
                corrected_exists = any(
                    self.hass.states.get(corrected + suffix) is not None
                    for suffix in ("_temperature", "_humidity")
                )
                if not old_exists and corrected_exists:
                    prefix = corrected
            prefixes.append(prefix)
        return tuple(prefixes)

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

    async def _hourly_weather(self, now):
        if (
            self._weather_at is not None
            and (now - self._weather_at).total_seconds() < 1800
        ):
            return self._weather_hours
        self._weather_at = now
        self._weather_hours = []
        entity = self._entity("hvac_weather")
        state = self.hass.states.get(entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return []
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            for row in (response or {}).get(entity, {}).get("forecast", []):
                at = dt_util.parse_datetime(row.get("datetime", ""))
                if at is not None and at.tzinfo is not None:
                    self._weather_hours.append(
                        {
                            "at": at.timestamp(),
                            "temperature_c": celsius(
                                row.get("temperature"),
                                state.attributes.get("temperature_unit"),
                            ),
                            "humidity": number(row.get("humidity")),
                        }
                    )
        except Exception:
            _LOGGER.warning(
                "HVAC hourly weather unavailable; retaining baseline forecast",
                exc_info=True,
            )
        return self._weather_hours

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
                self._hvac_energy.pop("previous", None)

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
            if self._hvac_memory is None:
                self._hvac_memory = await self._hvac_store.async_load() or {}
                # A restart is a gap, never evidence of continuous unmet demand.
                self._hvac_memory.pop("previous", None)

            prefixes = self._room_prefixes()
            identity = {k: self._entity(k) for k in DEFAULT_ENTITIES}
            identity["room_prefixes"] = ",".join(prefixes)
            if self._hvac_memory.get("entity_mapping") != identity:
                self._hvac_memory = {"entity_mapping": identity}
                self._weather_at = None

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
            sample = {
                "at": now.timestamp(),
                "day": now.date().isoformat(),
                "mode": thermostat.state if thermostat else None,
                "action": attrs.get("hvac_action"),
                "target_c": celsius(attrs.get("temperature"), temp_unit),
                "indoor_c": celsius(attrs.get("current_temperature"), temp_unit),
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

            # Never train through a room telemetry outage.
            if not room_health:
                sample["humidity"] = None

            diagnostics = observe(self._hvac_memory, sample)
            hours = await self._hourly_weather(now)
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
                hourly_shadow=shadow,
                hourly_supported_hours=supported_hours,
                hourly_forecast_hours=len(shadow),
                forecast_applied=False,
                thermostat_control_enabled=False,
                weather_available=bool(hours),
                entities={k: self._entity(k) for k in DEFAULT_ENTITIES},
                power_sources=power_sources,
                power_mapping_valid=power_mapping_valid,
            )
            if not room_health:
                diagnostics.update(
                    status="unavailable",
                    reason="Indoor room data missing or stale",
                )

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
