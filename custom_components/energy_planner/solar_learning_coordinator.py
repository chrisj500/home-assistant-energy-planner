"""Persistent, observational AC solar learner; never changes planner decisions."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import logging

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_ACTUAL_SOLAR_POWER
from .coordinator import _solar_window
from .forecast_solar_shadow import interval_points_from_payload, integrate_interval_energy_kwh, weather_rows
from .headroom import correct_current_day_points
from .hvac_coordinator import EnergyPlannerHVACCoordinator
from .solar_learning import finalize, issue, lead_bucket, number, observe, scorecard, sky_bucket

_LOGGER = logging.getLogger(__name__)
DEFAULT_SOLAR_SOURCES = {
    "solar_learning_radiation_entity": "sensor.gw3000b_solar_radiation",
    "solar_learning_temperature_entity": "sensor.gw3000b_outdoor_temperature",
}


class EnergyPlannerSolarLearningCoordinator(EnergyPlannerHVACCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._solar_learning_store = Store(hass, 1, f"energy_planner.{entry.entry_id}.solar_learning")
        self._solar_memory = None
        self._solar_saved_at = None

    def _reading(self, entity, kind, now):
        state = self.hass.states.get(entity) if entity else None
        if state is None:
            return None
        value = number(state.state)
        reported = getattr(state, "last_reported", state.last_updated)
        if value is None or not 0 <= (now - reported).total_seconds() <= 300:
            return None
        unit = state.attributes.get("unit_of_measurement")
        if kind == "power":
            return value * (1000 if unit == "kW" else 1) if unit in ("W", "kW") and value >= 0 else None
        if kind == "energy":
            return value / (1000 if unit == "Wh" else 1) if unit in ("Wh", "kWh") and value >= 0 else None
        if kind == "temperature":
            return (value - 32) * 5 / 9 if unit == "°F" else value if unit == "°C" else None
        return value if unit in ("W/m²", "W/m2") and value >= 0 else None

    def _energy_entity(self):
        explicit = self.cfg.get("solar_learning_energy_entity")
        if explicit:
            return explicit
        candidates = [s.entity_id for s in self.hass.states.async_all()
                      if s.entity_id.startswith("sensor.envoy_") and s.entity_id.endswith("_lifetime_energy_production")]
        return candidates[0] if len(candidates) == 1 else None

    async def _solar_update(self, now):
        cfg = self.cfg
        if not cfg.get("solar_learning_enabled", True):
            if self._solar_memory is not None:
                self._solar_memory.pop("previous", None)
            return {"solar_learning_status": "disabled", "solar_learning_diagnostics": {"forecast_applied": False}}
        if self._solar_memory is None:
            loaded = await self._solar_learning_store.async_load()
            self._solar_memory = loaded if isinstance(loaded, dict) else {}
            self._solar_memory.pop("previous", None)  # Never integrate across a restart.
        memory = self._solar_memory
        power_entity = cfg.get(CONF_ACTUAL_SOLAR_POWER)
        energy_entity = self._energy_entity()
        sources = {key: cfg.get(key, default) for key, default in DEFAULT_SOLAR_SOURCES.items()}
        source, _ = self._forecast_solar_source()
        signature = self._estimate_source_signature(source) if source else None
        identity = sha256(json.dumps({"power": power_entity, "energy": energy_entity,
                                     "weather": sources, "provider": signature}, sort_keys=True).encode()).hexdigest()
        # Do not reset historical learning merely because provider setup is temporarily unavailable.
        if source and memory.get("identity") not in (None, identity):
            memory.clear()
            memory["reset_reason"] = "production_or_forecast_source_changed"
        if source:
            memory["identity"] = identity
        stamp = now.timestamp()
        power = self._reading(power_entity, "power", now)
        energy = self._reading(energy_entity, "energy", now)
        radiation = self._reading(sources["solar_learning_radiation_entity"], "radiation", now)
        temperature = self._reading(sources["solar_learning_temperature_entity"], "temperature", now)
        valid = power is not None and not (power == 0 and radiation is not None and radiation >= 100)
        sun = self.hass.states.get("sun.sun")
        sun_attrs = sun.attributes if sun else {}
        sample = {"sun_elevation_deg": number(sun_attrs.get("elevation")),
                  "sun_azimuth_deg": number(sun_attrs.get("azimuth")), "at": stamp, "power_w": power, "energy_kwh": energy, "radiation_wm2": radiation,
                  "temperature_c": temperature, "valid": valid}
        observe(memory, sample)
        finalize(memory, stamp)
        points = interval_points_from_payload(self._estimate_payload, now, assume_utc=True)
        points = [p for p in points if number(p.watts) is not None]
        success = self._estimate_last_success
        forecast_fresh = success is not None and 0 <= (now - success).total_seconds() <= 7200
        weather = weather_rows(self._professional_cache.get("weather"), now)
        weather_at = self._professional_attempts.get("weather")
        weather_fresh = bool(weather and weather_at and not self._professional_cache.get("weather_error")
                             and 0 <= (now - weather_at).total_seconds() <= 7200)
        if not weather_fresh:
            weather = []
        issued = False
        last_issue = memory.get("last_issue")
        if forecast_fresh and len(points) >= 2 and (last_issue is None or stamp - last_issue >= 3600):
            sunrise, sunset = _solar_window(self.hass, now.date())
            live = correct_current_day_points(points=points, reference=now, sunrise=sunrise, sunset=sunset,
                                               corrected_remaining_kwh=None, actual_solar_w=power if valid else None).points
            candidates = []
            first = (int(stamp) // 3600 + 1) * 3600
            for offset in range(24):
                start = datetime.fromtimestamp(first + offset * 3600, now.tzinfo)
                end = datetime.fromtimestamp(start.timestamp() + 3600, now.tzinfo)
                if start < points[0].at or end > points[-1].at:
                    continue  # Never score extrapolated zero beyond the provider horizon.
                # Missing interval blocks are not usable forecasts.
                if any((b.at - a.at).total_seconds() > 7200 for a, b in zip(points, points[1:])
                       if a.at < end and b.at > start):
                    continue
                matching_weather = [r for r in weather if abs((r["_at"] - start).total_seconds()) <= 1800]
                row = min(matching_weather, key=lambda r: abs((r["_at"] - start).total_seconds())) if matching_weather else {}
                sky = number(row.get("sky"))
                sky = sky if sky is not None and 0 <= sky <= 1 else None
                candidates.append({"issued_at": stamp, "provider_received_at": success.timestamp(),
                    "start": start.timestamp(), "end": end.timestamp(), "day": start.date().isoformat(),
                    "hour": start.hour, "lead": lead_bucket((end.timestamp() - stamp) / 3600),
                    "raw_kwh": integrate_interval_energy_kwh(points, start, end),
                    "live_kwh": integrate_interval_energy_kwh(live, start, end),
                    "sky_bin": sky_bucket(sky), "forecast_sky": sky,
                    "forecast_temperature_c": number(row.get("temperature")),
                    "weather_received_at": weather_at.timestamp() if weather_fresh else None,
                    "observed_at_issue": sample})
            issue(memory, candidates)
            issued = bool(candidates)
        pending = memory.get("pending", [])
        newest = [r for r in pending if r["issued_at"] == memory.get("last_issue")]
        stats = scorecard(memory)
        state = "collecting" if valid else "production_unavailable"
        if not forecast_fresh:
            state = "forecast_unavailable"
        elif any(r["trained"] for r in newest):
            state = "shadow" if valid else state
        diagnostic = {"forecast_applied": False, "model": "hour_lead_cloud_residual_v1", "model_version": 1,
            "status": state, "sources": {**sources, "power": power_entity, "energy": energy_entity},
            "current_observation": sample, "weather_forecast_available": weather_fresh,
            "forecast_fresh": forecast_fresh, "pending_forecasts": len(pending),
            "scored_forecasts": len(memory.get("scored", [])), "metrics": stats,
            "last_issue": memory.get("last_issue"), "reset_reason": memory.get("reset_reason"),
            "hourly_shadow": newest, "retention_days": 90,
            "training_policy": "8 distinct target hours across 7 days per local-hour/lead/cloud group; bounded to +/-25%; never applied"}
        if issued or self._solar_saved_at is None or stamp - self._solar_saved_at >= 300:
            await self._solar_learning_store.async_save(memory)
            self._solar_saved_at = stamp
        return {"solar_learning_status": state, "solar_learning_diagnostics": diagnostic,
                "solar_learning_usable_days": stats["usable_days"],
                "solar_learning_scored_forecasts": stats["accepted_forecasts"]}

    async def _async_update_data(self):
        data = await super()._async_update_data()
        try:
            data.update(await self._solar_update(dt_util.now()))
        except Exception:
            _LOGGER.exception("Solar shadow learning failed; operational forecast unchanged")
            if self._solar_memory is not None:
                self._solar_memory.pop("previous", None)
            data.update(solar_learning_status="error", solar_learning_diagnostics={"forecast_applied": False, "reason": "See integration logs"})
        return data
