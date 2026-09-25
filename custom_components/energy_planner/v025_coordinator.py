from __future__ import annotations

from datetime import timedelta
from copy import deepcopy
import logging

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTUAL_SOLAR_POWER, CONF_BASE_LOAD_POWER, CONF_CAPACITY_KWH,
    CONF_CHARGE_LIMIT, CONF_SOC_1, CONF_SOC_2, CONF_SOC_3, CONF_SOC_WEIGHTS,
    CONF_SOLAR_REMAINING, CONF_EV_HOME, DEFAULT_CAPACITY_KWH, DEFAULT_WEIGHTS,
    DEFAULT_CHARGE_EFFICIENCY, OPT_CHARGE_EFFICIENCY,
    OPT_EV_SOLAR_ADVISORY_ENABLED, DEFAULT_EV_SOLAR_ADVISORY_ENABLED,
)
from .coordinator import _controller_settings, _num, _solar_window
from .enhanced_coordinator import _parse_weights
from .forecast_solar_shadow import IntervalPoint, interval_points_from_payload
from .headroom import correct_current_day_points
from .reliability import MIN_EVIDENCE_SAMPLES, evidence, gate, number, observe, suppress_actions, sunset_envelope
from .rolling_ev import DaylightWindow, simulate_rolling_days, choose_ev_charge_window
from .v022_coordinator import EnergyPlannerV022Coordinator

_LOGGER = logging.getLogger(__name__)


class EnergyPlannerV025Coordinator(EnergyPlannerV022Coordinator):
    """Scenario forecasts plus independently scored, persistent decision evidence."""

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._trust_store = Store(hass, 1, f"energy_planner.{entry.entry_id}.reliability")
        self._trust = None
        self._surplus_since = None
        self._last_live_at = None

    def _fresh_power(self, entity, now):
        state = self.hass.states.get(entity) if entity else None
        if state is None:
            return None
        reported = getattr(state, "last_reported", state.last_updated)
        if not 0 <= (now - reported).total_seconds() <= 300:
            return None
        value = number(state.state)
        unit = state.attributes.get("unit_of_measurement")
        if value is None or unit not in {"W", "kW"}:
            return None
        return value * (1000 if unit == "kW" else 1)

    def _scenarios(self, data, now):
        rows = data.get("rolling_day_plans") or []
        if not rows or not self._estimate_payload:
            raise ValueError("No rolling forecast")
        cfg = self.cfg
        capacity = float(cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        weights = _parse_weights(cfg.get(CONF_SOC_WEIGHTS, DEFAULT_WEIGHTS))
        capacities = tuple(capacity * w / sum(weights) for w in weights)
        socs = tuple(_num(self.hass, cfg.get(key)) for key in (CONF_SOC_1, CONF_SOC_2, CONF_SOC_3))
        limit = _num(self.hass, cfg.get(CONF_CHARGE_LIMIT))
        load = number(data.get("rolling_planning_base_load_w"))
        reserve = number(data.get("effective_reserve_floor"))
        if any(number(v) is None for v in (*socs, limit, load, reserve)) or capacity <= 0:
            raise ValueError("Missing physical inputs")
        windows = []
        for row in rows:
            day = dt_util.parse_date(row["date"])
            sunrise, sunset = _solar_window(self.hass, day)
            if sunrise is None or sunset is None:
                raise ValueError("Missing solar window")
            windows.append(DaylightWindow(day, sunrise, sunset))
        sunrise, sunset = _solar_window(self.hass, now.date())
        points = interval_points_from_payload(self._estimate_payload, now, assume_utc=True)
        points = correct_current_day_points(
            points=points, reference=now, sunrise=sunrise, sunset=sunset,
            corrected_remaining_kwh=_num(self.hass, cfg.get(CONF_SOLAR_REMAINING)),
            actual_solar_w=_num(self.hass, cfg.get(CONF_ACTUAL_SOLAR_POWER)),
        ).points
        if not points or any(number(p.watts) is None for p in points):
            raise ValueError("Invalid solar curve")
        records = self._trust["records"]
        if any(number(row.get(k)) is None for row in rows for k in ("sunset_soc_pct", "solar_kwh")):
            raise ValueError("Invalid rolling forecast")
        profiles = {row["date"]: evidence(records, (dt_util.parse_date(row["date"]) - now.date()).days)
                    for row in rows}
        efficiency = float(cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY))
        controller = _controller_settings(self.hass, 250.0)
        median_drop = number(data.get("calibration_overnight_median_kw")) or 0.0
        overnight = (self._calibration_data or {}).get("overnight_records", [])
        drops = [number(r.get("drop_rate_kw")) for r in overnight]
        drops = [v for v in drops if v is not None and v >= 0]
        for profile in profiles.values():
            learning_ready = (
                profile["samples"] >= MIN_EVIDENCE_SAMPLES
                and len(drops) >= MIN_EVIDENCE_SAMPLES
            )
            profile.update(
                learning_ready=learning_ready,
                sunset_samples=profile["samples"],
                sunset_samples_required=MIN_EVIDENCE_SAMPLES,
                overnight_samples=len(drops),
                overnight_samples_required=MIN_EVIDENCE_SAMPLES,
            )
            if not learning_ready:
                profile["confidence"] = "learning"
        # Stress assumptions are explicit engineering bounds, not learned HVAC.
        lower_points = [IntervalPoint(p.at, p.watts * .70) for p in points]
        upper_points = [IntervalPoint(p.at, p.watts * 1.30) for p in points]
        common = dict(reference=now, daylight_windows=windows, initial_bank_socs_pct=socs,
                      bank_capacities_kwh=capacities, charge_limit_pct=limit,
                      reserve_pct=reserve, charge_efficiency=efficiency,
                      controller=controller, step_minutes=5)
        nominal = simulate_rolling_days(
            points=points,
            average_load_kw=load / 1000,
            overnight_drop_kw=median_drop,
            **common,
        )
        low = simulate_rolling_days(points=lower_points, average_load_kw=load / 1000 * 1.30,
                                    overnight_drop_kw=max([median_drop * 1.30, *drops]), **common)
        high = simulate_rolling_days(points=upper_points, average_load_kw=load / 1000 * .70,
                                     overnight_drop_kw=min([median_drop * .70, *drops]), **common)
        if len(nominal) != len(rows) or len(low) != len(rows) or len(high) != len(rows):
            raise ValueError("Incomplete scenario horizon")
        candidate = None
        amount = 0.0
        reserve_floor = min(max(float(reserve), 0.0), 100.0)
        window_by_date = {window.day: window for window in windows}
        current_soc = sum(soc * cap for soc, cap in zip(socs, capacities)) / capacity
        for row, mid, lo, hi in zip(rows, nominal, low, high):
            profile = profiles[row["date"]]
            width = profile["width_soc"]
            window = window_by_date[lo.day]
            lower_soc, upper_soc, remaining_fraction = sunset_envelope(
                now=now, sunrise=window.sunrise, sunset=window.sunset,
                target_date=lo.day, current_soc=current_soc,
                point_soc=row["sunset_soc_pct"], low_soc=lo.end_soc_pct,
                high_soc=hi.end_soc_pct, historical_width=width,
                reserve_floor=reserve_floor,
            )

            is_today = lo.day == now.date()
            historical_mae = profile["mae_soc"]
            if historical_mae is None:
                display_uncertainty = None
            elif is_today:
                # Historical MAE describes a full-day sunset forecast. As today's
                # daylight becomes observed, only the unobserved share remains
                # uncertain. This converges to zero at sunset.
                display_uncertainty = round(
                    max(float(historical_mae) * remaining_fraction, 0.0),
                    1,
                )
            else:
                display_uncertainty = round(float(historical_mae), 1)

            display_source = (
                "live_anchored_interval_simulation"
                if is_today and window.sunrise <= now < window.sunset
                else "interval_simulation"
            )
            margin = max(2.0, capacity * .05, capacity * width / 100)
            robust = max(0.0, min(lo.headroom_shortfall_kwh,
                                  lo.capacity_export_kwh * efficiency) - margin)
            row.update({
                # This planner assumes grid-connected operation. EcoFlow reserve is
                # therefore a hard policy floor for displayed SOC scenarios.
                "sunset_soc_low_pct": lower_soc,
                "sunset_soc_high_pct": upper_soc,
                "range_remaining_daylight_fraction": remaining_fraction,
                # Human-facing point estimate. Unlike the safety envelope above,
                # this follows the live-anchored interval curve for the current day.
                "display_sunset_soc_pct": round(mid.end_soc_pct, 2),
                "display_uncertainty_pct": display_uncertainty,
                "display_forecast_source": display_source,
                "display_confidence": profile["confidence"],
                "confidence": profile["confidence"], "error_samples": profile["samples"],
                "historical_mae_soc": profile["mae_soc"], "safety_margin_kwh": round(margin, 2),
                "conservative_headroom_kwh": round(robust, 2),
                "range_kind": "scenario_envelope_not_probability_interval",
                "range_assumption": "grid_connected_reserve_enforced",
            })
            lead = (lo.day - now.date()).days
            if candidate is None and robust > 0 and lead <= 2:
                candidate, amount = row["date"], robust
        return profiles, candidate, amount, lower_points, windows, load * 1.30, efficiency

    def _score_forecasts(self, data, now):
        """One fixed snapshot per issue-day/target-day; settle only near sunset."""
        today = now.date().isoformat()
        _, sunset = _solar_window(self.hass, now.date())
        actual = number(data.get("weighted_soc"))
        pending = self._trust["pending"]
        for key, sample in list(pending.items()):
            if sample["date"] < today:
                del pending[key]  # Missed sunset: never substitute next morning SOC.
            elif sample["date"] == today and sunset and now >= sunset:
                if actual is not None and (now - sunset).total_seconds() <= 300:
                    sample = {**sample, "actual_soc": actual,
                              "error_soc": actual - sample["predicted_soc"]}
                    self._trust["records"].append(sample)
                del pending[key]
        for row in data.get("rolling_day_plans", []):
            target = dt_util.parse_date(row["date"])
            _, target_sunset = _solar_window(self.hass, target)
            if target_sunset is None or (target_sunset - now).total_seconds() < 6 * 3600:
                continue  # Do not inflate same-day accuracy with last-minute predictions.
            key = f"{today}/{row['date']}"
            pending.setdefault(key, {"date": row["date"], "issued_at": now.isoformat(),
                                     "lead": (target - now.date()).days,
                                     "predicted_soc": row["sunset_soc_pct"]})
        self._trust["records"] = self._trust["records"][-210:]

    async def _async_update_data(self):
        data = await super()._async_update_data()
        now = dt_util.now()
        if self._trust is None:
            self._trust = await self._trust_store.async_load() or {}
            for key, default in (("records", []), ("pending", {}), ("revisions", []), ("decisions", [])):
                self._trust.setdefault(key, default)
            # Require new confirmations after restart; keep scored forecasts.
            self._trust["revisions"] = []
        previous_trust = deepcopy(self._trust)
        status, reason = "unavailable", "Forecast inputs unavailable—do not act."

        # Learning evidence is persistent and independent of whether today's
        # battery simulation can be built. Never render a model-input outage as
        # "0/3" and imply that historical learning was erased.
        stored_sunset_samples = len(self._trust.get("records", []))
        overnight_records = (self._calibration_data or {}).get("overnight_records", [])
        stored_overnight_samples = len(
            [
                row for row in overnight_records
                if number(row.get("drop_rate_kw")) is not None
                and number(row.get("drop_rate_kw")) >= 0
            ]
        )
        data.update(
            forecast_confidence="learning",
            forecast_error_samples=stored_sunset_samples,
            forecast_learning_ready=False,
            forecast_learning_sunset_samples=stored_sunset_samples,
            forecast_learning_sunset_required=MIN_EVIDENCE_SAMPLES,
            forecast_learning_overnight_samples=stored_overnight_samples,
            forecast_learning_overnight_required=MIN_EVIDENCE_SAMPLES,
            forecast_learning_progress=(
                f"{stored_sunset_samples}/{MIN_EVIDENCE_SAMPLES} scored sunsets · "
                f"{stored_overnight_samples}/{MIN_EVIDENCE_SAMPLES} overnight records"
            ),
        )
        try:
            profiles, candidate, amount, points, windows, load, efficiency = self._scenarios(data, now)
            self._score_forecasts(data, now)
            target = candidate or next(iter(profiles))
            profile = profiles[target]
            revision = self._estimate_last_success
            fresh = revision is not None and 0 <= (now - revision).total_seconds() <= 7200
            history, unstable, stable, count = observe(
                self._trust["revisions"], now=now,
                revision=revision.isoformat() if revision else None,
                rows=data["rolling_day_plans"], candidate=candidate)
            self._trust["revisions"] = history
            status, reason = gate(
                fresh=fresh,
                unstable=unstable,
                confidence=profile["confidence"],
                candidate=candidate,
                stable=stable,
                storm=data.get("storm"),
                storm_entity=data.get("storm_warning_entity"),
                storm_state=data.get("storm_warning_state"),
            )
            if status == "learning":
                reason = (
                    "Forecast reliability is learning: "
                    f"{profile['sunset_samples']}/{profile['sunset_samples_required']} "
                    "scored sunset forecasts and "
                    f"{profile['overnight_samples']}/{profile['overnight_samples_required']} "
                    "overnight calibration records. Do not act yet."
                )
            if unstable:
                today_key = now.date().isoformat()
                for row in data["rolling_day_plans"]:
                    row["historical_confidence"] = row["confidence"]
                    row["confidence"] = "low"
                    # Global provider instability must still block actions, but it
                    # should not downgrade today's live-anchored display estimate.
                    if (
                        row.get("date") != today_key
                        or row.get("display_forecast_source")
                        != "live_anchored_interval_simulation"
                    ):
                        row["display_confidence"] = "low"
            data.update(
                forecast_confidence="low" if unstable else profile["confidence"],
                forecast_error_samples=profile["samples"],
                forecast_historical_mae_soc=profile["mae_soc"],
                forecast_confirmation_count=count,
                forecast_revision_at=revision.isoformat() if revision else None,
                forecast_learning_ready=profile["learning_ready"],
                forecast_learning_sunset_samples=profile["sunset_samples"],
                forecast_learning_sunset_required=profile["sunset_samples_required"],
                forecast_learning_overnight_samples=profile["overnight_samples"],
                forecast_learning_overnight_required=profile["overnight_samples_required"],
                forecast_learning_progress=(
                    f"{profile['sunset_samples']}/{profile['sunset_samples_required']} "
                    "scored sunsets · "
                    f"{profile['overnight_samples']}/{profile['overnight_samples_required']} "
                    "overnight records"
                ),
            )
            # Always close old advice before selectively publishing verified advice.
            suppress_actions(data, status, reason)
            if status == "ready":
                for row in data["rolling_day_plans"]:
                    if row["date"] == candidate:
                        row.update(dynamic_load_needed=True, dynamic_load_needed_kwh=amount / efficiency,
                                   status="confirmed_headroom_risk")
                data.update(rolling_dynamic_load_days_count=1, rolling_dynamic_load_risk_dates=[candidate],
                            rolling_dynamic_load_total_kwh=amount / efficiency,
                            rolling_dynamic_load_next_3d_kwh=amount / efficiency,
                            rolling_dynamic_load_forecast_status="confirmed_headroom_risk")
                data.update(authoritative_headroom_risk=candidate == now.date().isoformat(),
                            authoritative_headroom_status="risk_today" if candidate == now.date().isoformat() else "risk_future",
                            authoritative_headroom_risk_date=candidate,
                            authoritative_headroom_shortfall_kwh=amount,
                            authoritative_headroom_action="Conservative headroom risk confirmed. EV charging still requires a verified solar window.")
                self._verified_ev(data, now, candidate, amount, points, windows, load, efficiency)
            else:
                self._surplus_since = None
        except Exception as err:
            _LOGGER.exception("Reliability evaluation failed; withholding all discretionary advice")
            battery_reason = data.get("battery_outlook_reason")
            if data.get("battery_outlook_status") == "unavailable" and battery_reason:
                reason = f"Battery outlook unavailable: {battery_reason}—do not act."
            elif isinstance(err, ValueError) and str(err):
                reason = f"Reliability evaluation unavailable: {err}—do not act."
            else:
                reason = "Reliability evaluation unavailable—do not act."
            status = "unavailable"
            data["forecast_reliability_error"] = {
                "type": type(err).__name__,
                "message": str(err),
                "battery_outlook_status": data.get("battery_outlook_status"),
                "battery_outlook_reason": battery_reason,
            }
            suppress_actions(data, status, reason)
            self._surplus_since = None
        data.update(forecast_reliability_status=status, forecast_reliability_reason=reason)
        decision = {"status": status, "reason": reason,
                    "ev_reason": data.get("rolling_ev_auto_charge_reason"),
                    "confidence": data.get("forecast_confidence"),
                    "planning_load_w": data.get("rolling_planning_base_load_w"),
                    "planning_load_source": data.get("rolling_planning_base_load_source"),
                    "storm": data.get("storm"),
                    "revision": data.get("forecast_revision_at")}
        if not self._trust["decisions"] or any(self._trust["decisions"][-1].get(k) != v for k, v in decision.items()):
            self._trust["decisions"].append({**decision, "at": now.isoformat(),
                                             "days": data.get("rolling_day_plans", [])})
            self._trust["decisions"] = self._trust["decisions"][-100:]
        if self._trust != previous_trust:
            await self._trust_store.async_save(self._trust)
        return data

    def _verified_ev(self, data, now, candidate, amount, points, windows, load, efficiency):
        power = number(data.get("rolling_ev_charge_power_w"))
        available = number(data.get("rolling_ev_available_energy_kwh"))
        # Missing home telemetry must not be interpreted as home.
        home = self.hass.states.get(self.cfg.get(CONF_EV_HOME, ""))
        enabled = self.cfg.get(OPT_EV_SOLAR_ADVISORY_ENABLED, DEFAULT_EV_SOLAR_ADVISORY_ENABLED)
        if not enabled or home is None or home.state != "home" or data.get("rolling_ev_soc_data_status") != "fresh" or not power or not available:
            data["rolling_ev_auto_charge_reason"] = "EV advice requires enabled advisory, fresh SOC, known home status, and charge power."
            self._surplus_since = None
            return
        # Validate a window starting NOW, avoiding a moving best-window target.
        energy = min(available, amount / efficiency)
        latest = min(next(w.sunset for w in windows if w.day.isoformat() == candidate),
                     now + timedelta(hours=energy / (power / 1000)))
        window = choose_ev_charge_window(points=points, daylight_windows=windows,
            earliest=now, latest=latest, base_load_kw=load / 1000, charge_power_w=power,
            energy_kwh=energy, charge_efficiency=efficiency,
            step_minutes=15)
        if window is None or window.solar_energy_kwh / max(window.requested_energy_kwh, .001) < .90:
            data["rolling_ev_auto_charge_reason"] = "No conservative charging window is at least 90% solar supplied."
            self._surplus_since = None
            return
        solar = self._fresh_power(self.cfg.get(CONF_ACTUAL_SOLAR_POWER), now)
        base = self._fresh_power(self.cfg.get(CONF_BASE_LOAD_POWER), now)
        live_ok = solar is not None and base is not None and solar - max(base, load) >= power + 500
        in_window = window.start <= now <= window.end
        if not live_ok or not in_window or (self._last_live_at and (now - self._last_live_at).total_seconds() > 120):
            self._surplus_since = None
        self._last_live_at = now
        if live_ok and in_window:
            self._surplus_since = self._surplus_since or now
        verified = self._surplus_since is not None and (now - self._surplus_since).total_seconds() >= 600
        if not verified:
            data["rolling_ev_auto_charge_reason"] = "Wait: charging needs an active conservative window and ten minutes of measured surplus covering EV power plus 500 W."
            return
        reason = "Charge within the verified window: conservative headroom need and sustained measured solar surplus confirmed."
        data.update(rolling_ev_status="green", rolling_ev_status_reason=reason,
                    rolling_ev_auto_charge_eligible=True, rolling_ev_auto_charge_reason=reason,
                    rolling_ev_recommended_energy_kwh=window.requested_energy_kwh,
                    rolling_ev_window_start=window.start.isoformat(), rolling_ev_window_end=window.end.isoformat(),
                    rolling_ev_window_solar_kwh=window.solar_energy_kwh,
                    rolling_ev_window_grid_kwh=window.grid_energy_kwh,
                    rolling_ev_window_solar_fraction_pct=100 * window.solar_energy_kwh / window.requested_energy_kwh,
                    rolling_ev_headroom_preserved_kwh=window.preserved_stationary_headroom_kwh,
                    ev_plan=reason)
