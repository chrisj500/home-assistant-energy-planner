from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_LOAD_POWER,
    CONF_EV_CHARGING_POWER,
    CONF_EV_HOME,
    CONF_EV_SOC,
)
from .coordinator import _num, _solar_window
from .enhanced_coordinator import (
    EnhancedEnergyPlannerCoordinator,
    _MAX_EV_ENERGY_SAMPLES,
    _MAX_EV_POWER_SAMPLES,
    _MAX_SAMPLE_GAP_SECONDS,
)
from .ev_advisory import (
    auto_charge_eligibility,
    classify_ev_charging_outlook,
    ev_soc_data_status,
)
from .ev_learning import infer_wall_energy_full_kwh
from .forecast_solar_shadow import (
    forecast_horizon_days,
    integrate_interval_energy_kwh,
    interval_points_from_payload,
    safe_float,
)

_SOC_WAIT_AFTER_CHARGE = timedelta(hours=2)


class EnergyPlannerV014Coordinator(EnhancedEnergyPlannerCoordinator):
    """v0.1.14 EV outlook, future-executor intent, and resilient EV learning."""

    def _ev_power_w(self) -> float | None:
        entity_id = self.cfg.get(CONF_EV_CHARGING_POWER)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        value = _num(self.hass, entity_id)
        if value is None:
            return None
        unit = "" if state is None else str(state.attributes.get("unit_of_measurement", ""))
        if unit.lower() == "kw":
            return float(value) * 1000.0
        return float(value)

    def _ev_soc_age_minutes(self) -> float | None:
        entity_id = self.cfg.get(CONF_EV_SOC)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return max((dt_util.now() - state.last_updated).total_seconds() / 60.0, 0.0)

    async def _update_ev_learning(self) -> None:
        """Learn EV power immediately and wait for delayed vehicle SOC telemetry."""
        data = await self._ensure_ev_learning_data()
        if not self.cfg.get(CONF_EV_CHARGING_POWER):
            return

        now = dt_util.now()
        power = self._ev_power_w()
        ev_soc = _num(self.hass, self.cfg.get(CONF_EV_SOC))
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
                    "latest_soc_pct": ev_soc,
                    "wall_energy_kwh": 0.0,
                    "last_at": now.isoformat(),
                    "last_power_w": float(power),
                    "stopped_at": None,
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
                session["stopped_at"] = None
                if ev_soc is not None:
                    session["latest_soc_pct"] = ev_soc
            data["session"] = session

        elif isinstance(session, dict):
            stopped_at = dt_util.parse_datetime(str(session.get("stopped_at", "")))
            if stopped_at is None:
                last_at = dt_util.parse_datetime(str(session.get("last_at", "")))
                try:
                    last_power = float(session.get("last_power_w", 0.0))
                    wall_energy = float(session.get("wall_energy_kwh", 0.0))
                except (TypeError, ValueError):
                    last_power = 0.0
                    wall_energy = 0.0
                if last_at is not None:
                    elapsed_s = max((now - last_at).total_seconds(), 0.0)
                    if 0.0 < elapsed_s <= _MAX_SAMPLE_GAP_SECONDS:
                        wall_energy += last_power * elapsed_s / 3_600_000.0
                session["wall_energy_kwh"] = wall_energy
                session["stopped_at"] = now.isoformat()
                stopped_at = now
                changed = True

            if ev_soc is not None:
                session["latest_soc_pct"] = ev_soc
            inferred = infer_wall_energy_full_kwh(
                wall_energy_kwh=float(session.get("wall_energy_kwh", 0.0) or 0.0),
                start_soc_pct=safe_float(session.get("start_soc_pct")),
                end_soc_pct=safe_float(session.get("latest_soc_pct")),
            )
            if inferred is not None:
                samples = [
                    float(value)
                    for value in data.get("wall_full_samples_kwh", [])
                    if 5.0 <= float(value) <= 250.0
                ]
                samples.append(inferred)
                data["wall_full_samples_kwh"] = samples[-_MAX_EV_ENERGY_SAMPLES:]
                data["session"] = None
                changed = True
            elif stopped_at is not None and now - stopped_at >= _SOC_WAIT_AFTER_CHARGE:
                data["session"] = None
                changed = True
            else:
                data["session"] = session
                changed = True

        if changed:
            await self._save_ev_learning_data()

    def _solar_surplus_outlook(self, *, now, planning_load_w: float | None) -> tuple[float, float]:
        if self._estimate_payload is None or planning_load_w is None:
            return 0.0, 0.0
        points = interval_points_from_payload(self._estimate_payload, now, assume_utc=True)
        if len(points) < 2:
            return 0.0, 0.0
        horizon = min(max(forecast_horizon_days(points, now), 1), 7)
        next_3d = 0.0
        total = 0.0
        for offset in range(horizon):
            target_date = now.date() + timedelta(days=offset)
            sunrise, sunset = _solar_window(self.hass, target_date)
            if sunrise is None or sunset is None or sunset <= now:
                continue
            start = max(now, sunrise) if offset == 0 else sunrise
            if start >= sunset:
                continue
            duration_h = (sunset - start).total_seconds() / 3600.0
            solar = integrate_interval_energy_kwh(points, start, sunset, step_minutes=15)
            load = max(float(planning_load_w), 0.0) / 1000.0 * duration_h
            surplus = max(solar - load, 0.0)
            total += surplus
            if offset <= 2:
                next_3d += surplus
        return next_3d, total

    def _rolling_ev_outputs(self, baseline: dict[str, Any]) -> dict[str, Any]:
        output = super()._rolling_ev_outputs(baseline)
        now = dt_util.now()
        detailed_status = str(output.get("rolling_ev_status", "unavailable"))

        risk_date = None
        raw_risk = output.get("rolling_headroom_risk_date")
        if isinstance(raw_risk, str) and raw_risk not in {"", "none"}:
            try:
                risk_date = dt_util.parse_date(raw_risk)
            except (TypeError, ValueError):
                risk_date = None

        requested = safe_float(output.get("rolling_ev_recommended_energy_kwh"))
        solar = safe_float(output.get("rolling_ev_window_solar_kwh"))
        solar_fraction = None
        if requested is not None and requested > 0 and solar is not None:
            solar_fraction = min(max(solar / requested, 0.0), 1.0)

        planning_load = safe_float(output.get("rolling_planning_base_load_w"))
        next_3d, horizon = self._solar_surplus_outlook(
            now=now,
            planning_load_w=planning_load,
        )
        outlook = classify_ev_charging_outlook(
            reference_date=now.date(),
            risk_date=risk_date,
            best_solar_fraction=solar_fraction,
            surplus_next_3d_kwh=next_3d,
            surplus_horizon_kwh=horizon,
        )

        ev_home = None
        home_entity = self.cfg.get(CONF_EV_HOME)
        if home_entity:
            state = self.hass.states.get(home_entity)
            ev_home = None if state is None else state.state == "home"

        start = dt_util.parse_datetime(str(output.get("rolling_ev_window_start") or ""))
        end = dt_util.parse_datetime(str(output.get("rolling_ev_window_end") or ""))
        eligible, eligible_reason = auto_charge_eligibility(
            outlook_status=outlook.status,
            now=now,
            window_start=start,
            window_end=end,
            ev_home=ev_home,
            available_energy_kwh=safe_float(output.get("rolling_ev_available_energy_kwh")),
            solar_fraction=solar_fraction,
        )

        current_power = self._ev_power_w()
        soc_age = self._ev_soc_age_minutes()
        output.update(
            {
                "rolling_ev_status": outlook.status,
                "rolling_ev_advisory_detail": detailed_status,
                "rolling_ev_status_reason": outlook.reason,
                "rolling_ev_days_to_risk": outlook.days_to_risk,
                "rolling_ev_surplus_next_3d_kwh": next_3d,
                "rolling_ev_surplus_horizon_kwh": horizon,
                "rolling_ev_window_solar_fraction_pct": (
                    solar_fraction * 100.0 if solar_fraction is not None else None
                ),
                "rolling_ev_auto_charge_eligible": eligible,
                "rolling_ev_auto_charge_reason": eligible_reason,
                "rolling_ev_power_entity": self.cfg.get(CONF_EV_CHARGING_POWER),
                "rolling_ev_current_power_w": current_power,
                "rolling_ev_soc_age_minutes": soc_age,
                "rolling_ev_soc_data_status": ev_soc_data_status(
                    soc_age,
                    charging=current_power is not None and current_power >= 500.0,
                ),
                "rolling_ev_learning_status": self._ev_learning_status(),
                "rolling_ev_model": "forecast_solar_paid_raw_rolling_v3",
            }
        )
        return output

    def _ev_learning_status(self) -> str:
        learning = self._ev_learning_data or {}
        session = learning.get("session")
        if not isinstance(session, dict):
            return "idle"
        if self._ev_power_w() is not None and self._ev_power_w() >= 500.0:
            start = safe_float(session.get("start_soc_pct"))
            latest = safe_float(session.get("latest_soc_pct"))
            if start is None or latest is None or latest - start < 5.0:
                return "charging_waiting_for_soc_update"
            return "charging_learning"
        return "charge_complete_waiting_for_soc_update"
