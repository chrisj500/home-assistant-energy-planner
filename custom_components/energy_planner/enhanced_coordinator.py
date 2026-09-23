from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_LOAD_24H,
    CONF_BASE_LOAD_3H,
    CONF_BASE_LOAD_POWER,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_LIMIT,
    CONF_EV_CHARGING_POWER,
    CONF_EV_HOME,
    CONF_EV_SOC,
    CONF_EXPECTED_LOAD_REMAINING,
    CONF_FORECAST_SOLAR_API_KEY,
    CONF_SOC_1,
    CONF_SOC_2,
    CONF_SOC_3,
    CONF_SOC_WEIGHTS,
    CONF_SOLAR_REMAINING,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_EFFICIENCY,
    DEFAULT_EV_FALLBACK_CHARGE_POWER_W,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_EV_WALL_KWH_FULL,
    DEFAULT_PREFERRED_IMPORT_W,
    DEFAULT_WEIGHTS,
    OPT_CHARGE_EFFICIENCY,
    OPT_EV_FALLBACK_CHARGE_POWER_W,
    OPT_EV_TARGET_SOC,
    OPT_EV_WALL_KWH_FULL,
    OPT_PREFERRED_IMPORT_W,
)
from .coordinator import EnergyPlannerCoordinator, _controller_settings, _num, _solar_window
from .ev_learning import (
    infer_wall_energy_full_kwh,
    learned_wall_energy_full_kwh,
    residual_after_ev_charge,
)
from .forecast_solar_shadow import (
    forecast_horizon_days,
    integrate_interval_energy_kwh,
    interval_points_from_payload,
    interval_resolution_minutes,
    power_at,
    safe_float,
    weather_rows,
)
from .rolling_ev import (
    DaylightWindow,
    choose_ev_charge_window,
    ev_wall_energy_to_target_kwh,
    first_headroom_risk,
    learned_charge_power_w,
    planning_base_load_w,
    simulate_rolling_days,
)
from .simulation import simulate_energy_flow

FORECAST_SOLAR_DOMAIN = "forecast_solar"
FORECAST_SOLAR_BASE_URL = "https://api.forecast.solar"
_MIN_RAW_ENERGY_KWH = 0.05
_ESTIMATE_REFRESH = timedelta(minutes=30)
_PROFESSIONAL_REFRESH = timedelta(minutes=30)
_API_CALL_SPACING = timedelta(minutes=5)
_REQUEST_TIMEOUT_SECONDS = 20
_EV_LEARNING_STORE_VERSION = 1
_ESTIMATE_CACHE_STORE_VERSION = 1
_MAX_EV_POWER_SAMPLES = 120
_MAX_EV_ENERGY_SAMPLES = 20
_MAX_SAMPLE_GAP_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class ForecastSolarSource:
    latitude: float
    longitude: float
    plane_path: str
    params: dict[str, str]


def _parse_weights(raw: Any) -> tuple[float, float, float]:
    try:
        values = tuple(float(part.strip()) for part in str(raw).split(","))
    except (TypeError, ValueError):
        values = (3.0, 2.0, 3.0)
    if len(values) != 3 or sum(values) <= 0:
        return (3.0, 2.0, 3.0)
    return values


class EnhancedEnergyPlannerCoordinator(EnergyPlannerCoordinator):
    """Energy Planner plus optional Forecast.Solar and rolling EV observability.

    Enhanced data remains advisory. Missing or expired credentials, subscription
    changes, rate limiting, provider failures, malformed interval data, or EV
    configuration gaps leave the inherited baseline planner untouched.
    """

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry)
        self._estimate_payload: dict[str, Any] | None = None
        self._estimate_last_success: datetime | None = None
        self._estimate_last_attempt: datetime | None = None
        self._estimate_error: str | None = None
        self._last_api_call_at: datetime | None = None
        self._professional_cache: dict[str, Any] = {}
        self._professional_attempts: dict[str, datetime] = {}
        self._professional_entitled: bool | None = None
        self._active_api_key: str | None = None
        self._estimate_origin = "none"
        self._estimate_cache_loaded = False
        self._estimate_cache_store: Store[dict[str, Any]] = Store(
            hass,
            _ESTIMATE_CACHE_STORE_VERSION,
            f"energy_planner.{entry.entry_id}.forecast_estimate",
        )
        self._ev_learning_store: Store[dict[str, Any]] = Store(
            hass,
            _EV_LEARNING_STORE_VERSION,
            f"energy_planner.{entry.entry_id}.ev_learning",
        )
        self._ev_learning_data: dict[str, Any] | None = None

    @staticmethod
    def _estimate_source_signature(source: ForecastSolarSource) -> dict[str, Any]:
        return {
            "latitude": source.latitude,
            "longitude": source.longitude,
            "plane_path": source.plane_path,
            "params": dict(sorted(source.params.items())),
        }

    async def _restore_estimate_cache(self, source: ForecastSolarSource) -> None:
        """Restore the last good interval forecast after HA/integration restart."""
        if self._estimate_cache_loaded:
            return
        self._estimate_cache_loaded = True
        loaded = await self._estimate_cache_store.async_load()
        if not isinstance(loaded, dict):
            return
        if loaded.get("source") != self._estimate_source_signature(source):
            return
        payload = loaded.get("payload")
        last_success = dt_util.parse_datetime(str(loaded.get("last_success", "")))
        if not isinstance(payload, dict) or last_success is None:
            return
        self._estimate_payload = payload
        self._estimate_last_success = last_success
        self._estimate_origin = "cache"

    async def _save_estimate_cache(self, source: ForecastSolarSource) -> None:
        if self._estimate_payload is None or self._estimate_last_success is None:
            return
        await self._estimate_cache_store.async_save(
            {
                "source": self._estimate_source_signature(source),
                "last_success": self._estimate_last_success.isoformat(),
                "payload": self._estimate_payload,
            }
        )

    async def _capture_daylight_forecast(
        self,
        *,
        target_date,
        predicted_gain_kwh: float,
    ) -> None:
        """Refresh the pre-sunrise calibration forecast until daylight starts."""
        data = await self._ensure_calibration_data()
        pending = data.get("daylight_pending")
        target = target_date.isoformat()
        if (
            isinstance(pending, dict)
            and pending.get("date") == target
            and "start_stored_kwh" in pending
        ):
            return
        data["daylight_pending"] = {
            "date": target,
            "predicted_gain_kwh": max(float(predicted_gain_kwh), 0.0),
            "captured_at": dt_util.now().isoformat(),
        }
        await self._save_calibration_data()

    async def _async_update_data(self) -> dict[str, Any]:
        baseline = await super()._async_update_data()
        try:
            enhancement = await self._forecast_solar_shadow(baseline)
        except Exception as err:  # Enhanced data must never break baseline planning.
            enhancement = self._fallback_shadow(
                reason=f"enhancement_error:{type(err).__name__}"
            )
        try:
            await self._update_ev_learning()
            rolling = self._rolling_ev_outputs(baseline)
        except Exception as err:  # Rolling advice is also non-authoritative.
            rolling = self._rolling_ev_fallback(f"rolling_error:{type(err).__name__}")
        return {**baseline, **enhancement, **rolling}

    async def _ensure_ev_learning_data(self) -> dict[str, Any]:
        if self._ev_learning_data is not None:
            return self._ev_learning_data
        loaded = await self._ev_learning_store.async_load()
        if not isinstance(loaded, dict):
            loaded = {}
        self._ev_learning_data = {
            "power_samples_w": list(loaded.get("power_samples_w", []))[-_MAX_EV_POWER_SAMPLES:],
            "wall_full_samples_kwh": list(loaded.get("wall_full_samples_kwh", []))[-_MAX_EV_ENERGY_SAMPLES:],
            "session": loaded.get("session") if isinstance(loaded.get("session"), dict) else None,
        }
        return self._ev_learning_data

    async def _save_ev_learning_data(self) -> None:
        if self._ev_learning_data is not None:
            await self._ev_learning_store.async_save(self._ev_learning_data)

    async def _update_ev_learning(self) -> None:
        """Persist charge-power observations and learn wall kWh per EV SOC point."""
        data = await self._ensure_ev_learning_data()
        cfg = self.cfg
        power_entity = cfg.get(CONF_EV_CHARGING_POWER)
        if not power_entity:
            return

        now = dt_util.now()
        power = _num(self.hass, power_entity)
        ev_soc = _num(self.hass, cfg.get(CONF_EV_SOC))
        session = data.get("session")
        charging = power is not None and power >= 500.0
        changed = False

        if charging:
            power_samples = [
                float(value)
                for value in data.get("power_samples_w", [])
                if float(value) >= 500.0
            ]
            power_samples.append(float(power))
            data["power_samples_w"] = power_samples[-_MAX_EV_POWER_SAMPLES:]
            changed = True

            if not isinstance(session, dict):
                session = {
                    "started_at": now.isoformat(),
                    "start_soc_pct": ev_soc,
                    "wall_energy_kwh": 0.0,
                    "last_at": now.isoformat(),
                    "last_power_w": float(power),
                }
            else:
                last_at = dt_util.parse_datetime(str(session.get("last_at", "")))
                try:
                    last_power = float(session.get("last_power_w", power))
                    wall_energy = float(session.get("wall_energy_kwh", 0.0))
                except (TypeError, ValueError):
                    last_power = float(power)
                    wall_energy = 0.0
                if last_at is not None:
                    elapsed_s = max((now - last_at).total_seconds(), 0.0)
                    if 0.0 < elapsed_s <= _MAX_SAMPLE_GAP_SECONDS:
                        wall_energy += ((last_power + float(power)) / 2.0) * elapsed_s / 3_600_000.0
                session["wall_energy_kwh"] = wall_energy
                session["last_at"] = now.isoformat()
                session["last_power_w"] = float(power)
            data["session"] = session

        elif isinstance(session, dict):
            inferred = infer_wall_energy_full_kwh(
                wall_energy_kwh=float(session.get("wall_energy_kwh", 0.0) or 0.0),
                start_soc_pct=safe_float(session.get("start_soc_pct")),
                end_soc_pct=ev_soc,
            )
            if inferred is not None:
                energy_samples = [
                    float(value)
                    for value in data.get("wall_full_samples_kwh", [])
                    if 5.0 <= float(value) <= 250.0
                ]
                energy_samples.append(inferred)
                data["wall_full_samples_kwh"] = energy_samples[-_MAX_EV_ENERGY_SAMPLES:]
            data["session"] = None
            changed = True

        if changed:
            await self._save_ev_learning_data()

    def _fallback_shadow(self, *, reason: str) -> dict[str, Any]:
        return {
            "forecast_solar_enhancement_status": "baseline_fallback",
            "forecast_solar_fallback_reason": reason,
            "forecast_solar_account_type": "unavailable",
            "forecast_solar_interval_resolution_min": None,
            "forecast_solar_horizon_days": None,
            "forecast_solar_interval_points": 0,
            "forecast_solar_paid_remaining_today_raw": None,
            "forecast_solar_shadow_projected_sunset_soc": None,
            "forecast_solar_shadow_sunset_soc_delta": None,
            "forecast_solar_shadow_charge_to_sunset": None,
            "forecast_solar_shadow_grid_import_today": None,
            "forecast_solar_shadow_export_today": None,
            "forecast_solar_shadow_scale_factor": None,
            "forecast_solar_shadow_model": "baseline_only",
            "forecast_solar_professional_status": "inactive",
            "forecast_solar_weather_sky_now_pct": None,
            "forecast_solar_weather_sky_next_6h_pct": None,
            "forecast_solar_weather_temperature_now": None,
            "forecast_solar_clearsky_remaining_today": None,
            "forecast_solar_weather_derate_pct": None,
            "forecast_solar_timewindow_count": None,
            "forecast_solar_best_window_start": None,
            "forecast_solar_best_window_end": None,
            "forecast_solar_best_window_power_w": None,
            "forecast_solar_best_window_energy_kwh": None,
        }

    def _rolling_ev_fallback(
        self,
        reason: str,
        *,
        planning_load_w: float | None = None,
        planning_source: str = "unavailable",
    ) -> dict[str, Any]:
        return {
            "rolling_ev_status": reason,
            "rolling_planning_base_load_w": planning_load_w,
            "rolling_planning_base_load_source": planning_source,
            "rolling_headroom_risk_date": "none",
            "rolling_headroom_shortfall_kwh": 0.0,
            "rolling_risk_projected_soc": None,
            "rolling_risk_export_kwh": 0.0,
            "rolling_ev_available_energy_kwh": None,
            "rolling_ev_charge_power_w": None,
            "rolling_ev_charge_power_samples": 0,
            "rolling_ev_charge_power_source": "unavailable",
            "rolling_ev_wall_full_kwh": None,
            "rolling_ev_wall_full_samples": 0,
            "rolling_ev_wall_full_source": "unavailable",
            "rolling_ev_recommended_energy_kwh": 0.0,
            "rolling_ev_window_start": None,
            "rolling_ev_window_end": None,
            "rolling_ev_window_solar_kwh": 0.0,
            "rolling_ev_window_grid_kwh": 0.0,
            "rolling_ev_headroom_preserved_kwh": 0.0,
            "rolling_ev_residual_headroom_shortfall_kwh": 0.0,
            "rolling_ev_residual_capacity_export_kwh": 0.0,
            "rolling_ev_model": "baseline_only",
        }

    def _forecast_solar_source(self) -> tuple[ForecastSolarSource | None, str | None]:
        entries = self.hass.config_entries.async_entries(FORECAST_SOLAR_DOMAIN)
        if not entries:
            return None, "forecast_solar_integration_not_configured"
        if len(entries) > 1:
            return None, "multiple_forecast_solar_entries"
        entry = entries[0]
        try:
            latitude = float(entry.data["latitude"])
            longitude = float(entry.data["longitude"])
        except (KeyError, TypeError, ValueError):
            return None, "forecast_solar_site_geometry_unavailable"

        get_planes = getattr(entry, "get_subentries_of_type", None)
        planes = list(get_planes("plane")) if callable(get_planes) else []
        if not planes:
            return None, "forecast_solar_plane_unavailable"

        path_parts: list[str] = []
        try:
            for plane in planes:
                declination = float(plane.data["declination"])
                azimuth = float(plane.data["azimuth"]) - 180.0
                kwp = float(plane.data["modules_power"]) / 1000.0
                path_parts.extend([f"{declination:g}", f"{azimuth:g}", f"{kwp:g}"])
        except (KeyError, TypeError, ValueError):
            return None, "forecast_solar_plane_geometry_invalid"

        params: dict[str, str] = {"time": "utc"}
        morning = entry.options.get("damping_morning")
        evening = entry.options.get("damping_evening")
        inverter_w = entry.options.get("inverter_size")
        if morning is not None:
            params["damping_morning"] = str(morning)
        if evening is not None:
            params["damping_evening"] = str(evening)
        try:
            if inverter_w is not None and float(inverter_w) > 0:
                params["inverter"] = str(float(inverter_w) / 1000.0)
        except (TypeError, ValueError):
            pass

        return ForecastSolarSource(
            latitude=latitude,
            longitude=longitude,
            plane_path="/".join(path_parts),
            params=params,
        ), None

    def _api_key(self) -> str | None:
        value = self.cfg.get(CONF_FORECAST_SOLAR_API_KEY)
        if value is None:
            return None
        key = str(value).strip()
        return key or None

    def _reset_sidecar_for_key(self, api_key: str) -> None:
        if api_key == self._active_api_key:
            return
        self._active_api_key = api_key
        self._estimate_payload = None
        self._estimate_last_success = None
        self._estimate_last_attempt = None
        self._estimate_error = None
        self._estimate_origin = "none"
        self._estimate_cache_loaded = False
        self._last_api_call_at = None
        self._professional_cache = {}
        self._professional_attempts = {}
        self._professional_entitled = None

    def _api_call_allowed(self, now: datetime) -> bool:
        return self._last_api_call_at is None or now - self._last_api_call_at >= _API_CALL_SPACING

    async def _api_get(
        self,
        *,
        api_key: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        session = async_get_clientsession(self.hass)
        url = f"{FORECAST_SOLAR_BASE_URL}/{api_key}/{path}"
        self._last_api_call_at = dt_util.now()
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
                async with session.get(url, params=params) as response:
                    if response.status in (401, 403):
                        return None, f"http_{response.status}"
                    if response.status == 429:
                        return None, "rate_limited"
                    if response.status >= 500:
                        return None, f"provider_http_{response.status}"
                    if response.status >= 400:
                        return None, f"http_{response.status}"
                    payload = await response.json(content_type=None)
        except TimeoutError:
            return None, "timeout"
        except Exception as err:
            return None, f"connection:{type(err).__name__}"
        if not isinstance(payload, dict):
            return None, "malformed_response"
        return payload, None

    async def _refresh_sidecar(
        self,
        *,
        api_key: str,
        source: ForecastSolarSource,
        now: datetime,
        average_baseload_w: float | None,
    ) -> None:
        del average_baseload_w
        retry_interval = _API_CALL_SPACING if self._estimate_error is not None else _ESTIMATE_REFRESH
        estimate_due = self._estimate_last_attempt is None or now - self._estimate_last_attempt >= retry_interval
        if estimate_due and self._api_call_allowed(now):
            self._estimate_last_attempt = now
            payload, error = await self._api_get(
                api_key=api_key,
                path=f"estimate/{source.latitude}/{source.longitude}/{source.plane_path}",
                params=source.params,
            )
            if payload is not None:
                self._estimate_payload = payload
                self._estimate_last_success = now
                self._estimate_error = None
                self._estimate_origin = "live"
                await self._save_estimate_cache(source)
            else:
                # A transient provider/API failure must not erase a previously
                # valid interval curve. Reliability freshness still gates advice,
                # while the battery outlook remains inspectable from cached data.
                self._estimate_error = error or "estimate_failed"
                if error in {"http_401", "http_403"}:
                    self._professional_entitled = False
            return

        if self._estimate_payload is None or not self._api_call_allowed(now):
            return

        last_attempt = self._professional_attempts.get("weather")
        if last_attempt is not None and now - last_attempt < _PROFESSIONAL_REFRESH:
            return
        self._professional_attempts["weather"] = now
        payload, error = await self._api_get(
            api_key=api_key,
            path=f"weather/{source.latitude}/{source.longitude}",
        )
        if payload is not None:
            self._professional_entitled = True
            self._professional_cache["weather"] = payload
            self._professional_cache["weather_error"] = None
        else:
            self._professional_cache["weather_error"] = error or "request_failed"
            if error in {"http_401", "http_403", "http_404"}:
                self._professional_entitled = False

    def _account_type(self) -> str:
        if self._professional_entitled is True:
            return "professional"
        payload = self._estimate_payload
        if not isinstance(payload, dict):
            return "unavailable"
        message = payload.get("message")
        if isinstance(message, dict):
            ratelimit = message.get("ratelimit")
            if isinstance(ratelimit, dict):
                limit = safe_float(ratelimit.get("limit"))
                if limit == 5:
                    return "professional"
                if limit == 60:
                    return "personal"
        return "keyed"

    async def _forecast_solar_shadow(self, baseline: dict[str, Any]) -> dict[str, Any]:
        api_key = self._api_key()
        if api_key is None:
            return self._fallback_shadow(reason="no_api_key_configured")
        if len(api_key) != 16 or not api_key.isalnum():
            return self._fallback_shadow(reason="api_key_format_invalid")
        self._reset_sidecar_for_key(api_key)

        source, source_error = self._forecast_solar_source()
        if source is None:
            return self._fallback_shadow(reason=source_error or "forecast_solar_source_unavailable")

        await self._restore_estimate_cache(source)
        now = dt_util.now()
        cfg = self.cfg
        remaining_solar = _num(self.hass, cfg.get(CONF_SOLAR_REMAINING))
        expected_load = _num(self.hass, cfg.get(CONF_EXPECTED_LOAD_REMAINING))
        charge_limit = _num(self.hass, cfg.get(CONF_CHARGE_LIMIT))
        soc_values = (
            _num(self.hass, cfg.get(CONF_SOC_1)),
            _num(self.hass, cfg.get(CONF_SOC_2)),
            _num(self.hass, cfg.get(CONF_SOC_3)),
        )
        _sunrise, sunset = _solar_window(self.hass, now.date())
        duration_h = max((sunset - now).total_seconds() / 3600.0, 0.0) if sunset is not None else 0.0
        average_baseload_w = (
            max(float(expected_load), 0.0) / duration_h * 1000.0
            if expected_load is not None and duration_h > 0
            else None
        )

        await self._refresh_sidecar(
            api_key=api_key,
            source=source,
            now=now,
            average_baseload_w=average_baseload_w,
        )

        if self._estimate_payload is None:
            return self._fallback_shadow(reason=self._estimate_error or "waiting_for_interval_forecast")
        if self._estimate_error in {"http_401", "http_403"}:
            return self._fallback_shadow(reason="api_key_rejected")

        points = interval_points_from_payload(self._estimate_payload, now, assume_utc=True)
        if len(points) < 2:
            return self._fallback_shadow(reason="interval_forecast_unavailable")

        resolution = interval_resolution_minutes(points)
        horizon = forecast_horizon_days(points, now)
        account_type = self._account_type()

        shadow = self._fallback_shadow(reason="live_projection_inputs_incomplete")
        shadow.update(
            {
                "forecast_solar_account_type": account_type,
                "forecast_solar_interval_resolution_min": resolution,
                "forecast_solar_horizon_days": horizon,
                "forecast_solar_interval_points": len(points),
                "forecast_solar_interval_source": self._estimate_origin,
                "forecast_solar_interval_last_success": (
                    self._estimate_last_success.isoformat()
                    if self._estimate_last_success is not None
                    else None
                ),
                "forecast_solar_interval_refresh_error": self._estimate_error,
            }
        )

        if sunset is not None and now < sunset:
            paid_raw_remaining = integrate_interval_energy_kwh(points, now, sunset)
            shadow["forecast_solar_paid_remaining_today_raw"] = paid_raw_remaining
        else:
            paid_raw_remaining = 0.0

        required = (
            remaining_solar,
            expected_load,
            charge_limit,
            soc_values[0],
            soc_values[1],
            soc_values[2],
            sunset,
        )
        if (
            any(value is None for value in required)
            or sunset is None
            or now >= sunset
            or paid_raw_remaining < _MIN_RAW_ENERGY_KWH
        ):
            shadow.update(self._professional_outputs(now))
            return shadow

        scale = max(float(remaining_solar), 0.0) / paid_raw_remaining
        weights = _parse_weights(cfg.get(CONF_SOC_WEIGHTS, DEFAULT_WEIGHTS))
        capacity = float(cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        total_weight = sum(weights)
        bank_capacities = tuple(capacity * weight / total_weight for weight in weights)
        bank_socs = tuple(float(value) for value in soc_values if value is not None)
        if len(bank_socs) != 3:
            shadow.update(self._professional_outputs(now))
            return shadow

        average_load_kw = max(float(expected_load), 0.0) / duration_h
        charge_efficiency = float(cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY))
        controller = _controller_settings(
            self.hass,
            float(cfg.get(OPT_PREFERRED_IMPORT_W, DEFAULT_PREFERRED_IMPORT_W)),
        )

        def scaled_solar(elapsed_h: float) -> float:
            at = now + timedelta(hours=max(float(elapsed_h), 0.0))
            return power_at(points, at) * scale / 1000.0

        simulation = simulate_energy_flow(
            duration_h=duration_h,
            solar_power_kw=scaled_solar,
            average_load_kw=average_load_kw,
            bank_socs_pct=bank_socs,
            bank_capacities_kwh=bank_capacities,
            charge_limit_pct=float(charge_limit),
            charge_efficiency=charge_efficiency,
            controller=controller,
            step_minutes=1,
        )

        baseline_sunset = baseline.get("projected_sunset_soc")
        delta = None
        if isinstance(baseline_sunset, (int, float)):
            delta = simulation.aggregate_soc_pct - float(baseline_sunset)

        shadow.update(
            {
                "forecast_solar_enhancement_status": "shadow_active",
                "forecast_solar_fallback_reason": "none",
                "forecast_solar_shadow_projected_sunset_soc": simulation.aggregate_soc_pct,
                "forecast_solar_shadow_sunset_soc_delta": delta,
                "forecast_solar_shadow_charge_to_sunset": simulation.stored_charge_kwh,
                "forecast_solar_shadow_grid_import_today": simulation.predicted_grid_import_kwh,
                "forecast_solar_shadow_export_today": simulation.predicted_export_kwh,
                "forecast_solar_shadow_scale_factor": scale,
                "forecast_solar_shadow_model": "forecast_solar_interval_scaled_shadow",
            }
        )
        shadow.update(self._professional_outputs(now))
        return shadow

    def _configured_or_default_sensor(self, configured: str | None, default_entity: str) -> float | None:
        value = _num(self.hass, configured)
        if value is not None:
            return value
        return _num(self.hass, default_entity)

    def _rolling_ev_outputs(self, baseline: dict[str, Any]) -> dict[str, Any]:
        if self._estimate_payload is None:
            return self._rolling_ev_fallback("waiting_for_interval_forecast")

        now = dt_util.now()
        points = interval_points_from_payload(self._estimate_payload, now, assume_utc=True)
        if len(points) < 2:
            return self._rolling_ev_fallback("interval_forecast_unavailable")

        cfg = self.cfg
        recent_3h = self._configured_or_default_sensor(
            cfg.get(CONF_BASE_LOAD_3H),
            "sensor.forecast_base_load_3h_average",
        )
        recent_24h = self._configured_or_default_sensor(
            cfg.get(CONF_BASE_LOAD_24H),
            "sensor.forecast_base_load_24h_average",
        )
        fallback_load = _num(self.hass, cfg.get(CONF_BASE_LOAD_POWER))
        planning_load_w, planning_source = planning_base_load_w(
            recent_3h,
            recent_24h,
            fallback_load,
        )
        if planning_load_w is None:
            return self._rolling_ev_fallback("planning_load_unavailable")

        soc_values = (
            _num(self.hass, cfg.get(CONF_SOC_1)),
            _num(self.hass, cfg.get(CONF_SOC_2)),
            _num(self.hass, cfg.get(CONF_SOC_3)),
        )
        if any(value is None for value in soc_values):
            return self._rolling_ev_fallback(
                "battery_soc_unavailable",
                planning_load_w=planning_load_w,
                planning_source=planning_source,
            )
        bank_socs = tuple(float(value) for value in soc_values if value is not None)
        if len(bank_socs) != 3:
            return self._rolling_ev_fallback(
                "battery_soc_unavailable",
                planning_load_w=planning_load_w,
                planning_source=planning_source,
            )

        charge_limit = _num(self.hass, cfg.get(CONF_CHARGE_LIMIT))
        if charge_limit is None:
            return self._rolling_ev_fallback(
                "charge_limit_unavailable",
                planning_load_w=planning_load_w,
                planning_source=planning_source,
            )

        weights = _parse_weights(cfg.get(CONF_SOC_WEIGHTS, DEFAULT_WEIGHTS))
        capacity = float(cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        total_weight = sum(weights)
        bank_capacities = tuple(capacity * weight / total_weight for weight in weights)
        reserve = baseline.get("effective_reserve_floor")
        reserve_pct = float(reserve) if isinstance(reserve, (int, float)) else 10.0
        overnight_drop_kw = baseline.get("calibration_overnight_median_kw")
        overnight_drop_kw = (
            float(overnight_drop_kw)
            if isinstance(overnight_drop_kw, (int, float))
            else 0.0
        )
        charge_efficiency = float(cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY))
        controller = _controller_settings(
            self.hass,
            float(cfg.get(OPT_PREFERRED_IMPORT_W, DEFAULT_PREFERRED_IMPORT_W)),
        )

        horizon = min(max(forecast_horizon_days(points, now), 1), 7)
        daylight_windows: list[DaylightWindow] = []
        for offset in range(horizon):
            target_date = now.date() + timedelta(days=offset)
            sunrise, sunset = _solar_window(self.hass, target_date)
            if sunrise is None or sunset is None:
                continue
            daylight_windows.append(
                DaylightWindow(day=target_date, sunrise=sunrise, sunset=sunset)
            )
        plans = simulate_rolling_days(
            points=points,
            reference=now,
            daylight_windows=daylight_windows,
            initial_bank_socs_pct=bank_socs,
            bank_capacities_kwh=bank_capacities,
            charge_limit_pct=float(charge_limit),
            reserve_pct=reserve_pct,
            charge_efficiency=charge_efficiency,
            controller=controller,
            average_load_kw=planning_load_w / 1000.0,
            overnight_drop_kw=overnight_drop_kw,
            step_minutes=5,
        )
        risk = first_headroom_risk(plans)

        learning = self._ev_learning_data or {}
        fallback_power = float(
            cfg.get(
                OPT_EV_FALLBACK_CHARGE_POWER_W,
                DEFAULT_EV_FALLBACK_CHARGE_POWER_W,
            )
        )
        ev_charge_power_w, power_samples = learned_charge_power_w(
            learning.get("power_samples_w", []),
            fallback_power,
        )
        power_source = "learned" if power_samples > 0 else "configured_fallback"

        fallback_full_kwh = float(cfg.get(OPT_EV_WALL_KWH_FULL, DEFAULT_EV_WALL_KWH_FULL))
        ev_wall_full_kwh, wall_full_samples = learned_wall_energy_full_kwh(
            learning.get("wall_full_samples_kwh", []),
            fallback_full_kwh,
        )
        wall_full_source = "learned" if wall_full_samples > 0 else "configured_fallback"

        ev_soc = _num(self.hass, cfg.get(CONF_EV_SOC))
        ev_target = float(cfg.get(OPT_EV_TARGET_SOC, DEFAULT_EV_TARGET_SOC))
        ev_available_energy = ev_wall_energy_to_target_kwh(
            current_soc_pct=ev_soc,
            target_soc_pct=ev_target,
            wall_kwh_full=ev_wall_full_kwh,
        )
        ev_home = None
        ev_home_entity = cfg.get(CONF_EV_HOME)
        if ev_home_entity:
            state = self.hass.states.get(ev_home_entity)
            ev_home = None if state is None else state.state == "home"

        output = {
            "rolling_ev_status": "no_headroom_risk",
            "rolling_planning_base_load_w": planning_load_w,
            "rolling_planning_base_load_source": planning_source,
            "rolling_headroom_risk_date": "none",
            "rolling_headroom_shortfall_kwh": 0.0,
            "rolling_risk_projected_soc": None,
            "rolling_risk_export_kwh": 0.0,
            "rolling_ev_available_energy_kwh": ev_available_energy,
            "rolling_ev_charge_power_w": ev_charge_power_w,
            "rolling_ev_charge_power_samples": power_samples,
            "rolling_ev_charge_power_source": power_source,
            "rolling_ev_wall_full_kwh": ev_wall_full_kwh,
            "rolling_ev_wall_full_samples": wall_full_samples,
            "rolling_ev_wall_full_source": wall_full_source,
            "rolling_ev_recommended_energy_kwh": 0.0,
            "rolling_ev_window_start": None,
            "rolling_ev_window_end": None,
            "rolling_ev_window_solar_kwh": 0.0,
            "rolling_ev_window_grid_kwh": 0.0,
            "rolling_ev_headroom_preserved_kwh": 0.0,
            "rolling_ev_residual_headroom_shortfall_kwh": 0.0,
            "rolling_ev_residual_capacity_export_kwh": 0.0,
            "rolling_ev_model": "forecast_solar_paid_raw_rolling_v2",
        }
        if risk is None:
            return output

        output.update(
            {
                "rolling_ev_status": "headroom_risk",
                "rolling_headroom_risk_date": risk.day.isoformat(),
                "rolling_headroom_shortfall_kwh": risk.headroom_shortfall_kwh,
                "rolling_risk_projected_soc": risk.end_soc_pct,
                "rolling_risk_export_kwh": risk.capacity_export_kwh,
                "rolling_ev_residual_headroom_shortfall_kwh": risk.headroom_shortfall_kwh,
                "rolling_ev_residual_capacity_export_kwh": risk.capacity_export_kwh,
            }
        )

        if ev_home is False:
            output["rolling_ev_status"] = "headroom_risk_ev_away"
            return output
        if ev_available_energy is None or ev_available_energy <= 0.05:
            output["rolling_ev_status"] = "headroom_risk_ev_full_or_unavailable"
            return output
        if ev_charge_power_w <= 0:
            output["rolling_ev_status"] = "headroom_risk_ev_power_unavailable"
            return output

        required_wall_kwh = max(
            risk.headroom_shortfall_kwh / max(charge_efficiency, 0.01),
            risk.capacity_export_kwh,
        )
        recommended_wall_kwh = min(required_wall_kwh, ev_available_energy)
        if recommended_wall_kwh <= 0.05:
            return output

        window = choose_ev_charge_window(
            points=points,
            daylight_windows=daylight_windows,
            earliest=now,
            latest=risk.end,
            base_load_kw=planning_load_w / 1000.0,
            charge_power_w=ev_charge_power_w,
            energy_kwh=recommended_wall_kwh,
            charge_efficiency=charge_efficiency,
            step_minutes=15,
        )
        if window is None:
            output["rolling_ev_status"] = "headroom_risk_no_ev_window"
            return output

        solar_fraction = window.solar_energy_kwh / max(window.requested_energy_kwh, 0.001)
        if solar_fraction < 0.80:
            output["rolling_ev_status"] = "headroom_risk_window_too_grid_heavy"
            return output

        residual_headroom, residual_export = residual_after_ev_charge(
            headroom_shortfall_kwh=risk.headroom_shortfall_kwh,
            capacity_export_kwh=risk.capacity_export_kwh,
            ev_solar_energy_kwh=window.solar_energy_kwh,
            charge_efficiency=charge_efficiency,
        )
        output.update(
            {
                "rolling_ev_status": "charge_window_recommended",
                "rolling_ev_recommended_energy_kwh": window.requested_energy_kwh,
                "rolling_ev_window_start": window.start.isoformat(),
                "rolling_ev_window_end": window.end.isoformat(),
                "rolling_ev_window_solar_kwh": window.solar_energy_kwh,
                "rolling_ev_window_grid_kwh": window.grid_energy_kwh,
                "rolling_ev_headroom_preserved_kwh": window.preserved_stationary_headroom_kwh,
                "rolling_ev_residual_headroom_shortfall_kwh": residual_headroom,
                "rolling_ev_residual_capacity_export_kwh": residual_export,
            }
        )
        return output

    def _professional_outputs(self, now: datetime) -> dict[str, Any]:
        weather = self._professional_cache.get("weather")
        rows = weather_rows(weather, now)
        sky_now = None
        temperature_now = None
        sky_next_6h = None
        if rows:
            past = [row for row in rows if row["_at"] <= now]
            current = past[-1] if past else rows[0]
            raw_sky = safe_float(current.get("sky"))
            sky_now = raw_sky * 100.0 if raw_sky is not None else None
            temperature_now = safe_float(current.get("temperature"))
            horizon = now + timedelta(hours=6)
            future_skies = [
                safe_float(row.get("sky"))
                for row in rows
                if now <= row["_at"] <= horizon
            ]
            future_skies = [value for value in future_skies if value is not None]
            if future_skies:
                sky_next_6h = sum(future_skies) / len(future_skies) * 100.0

        weather_error = self._professional_cache.get("weather_error")
        if self._professional_entitled is True:
            pro_status = "weather_available" if not weather_error else "weather_partial"
        elif self._professional_entitled is False:
            pro_status = "weather_not_entitled_or_key_rejected"
        else:
            pro_status = "weather_probing"

        return {
            "forecast_solar_professional_status": pro_status,
            "forecast_solar_weather_sky_now_pct": sky_now,
            "forecast_solar_weather_sky_next_6h_pct": sky_next_6h,
            "forecast_solar_weather_temperature_now": temperature_now,
            "forecast_solar_clearsky_remaining_today": None,
            "forecast_solar_weather_derate_pct": None,
            "forecast_solar_timewindow_count": None,
            "forecast_solar_best_window_start": None,
            "forecast_solar_best_window_end": None,
            "forecast_solar_best_window_power_w": None,
            "forecast_solar_best_window_energy_kwh": None,
        }
