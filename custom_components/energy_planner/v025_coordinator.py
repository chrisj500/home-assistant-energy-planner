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
from .counterfactual import (
    advance_counterfactual_ledger,
    counterfactual_bank_socs,
    live_capture_metrics,
)
from .enhanced_coordinator import _parse_weights
from .export_defense import assess_export_defense
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
        self._live_capture_since = None
        self._last_live_capture_at = None

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

    def _live_solar_surplus_w(self, data, now):
        solar = self._fresh_power(self.cfg.get(CONF_ACTUAL_SOLAR_POWER), now)
        configured_base = self._fresh_power(self.cfg.get(CONF_BASE_LOAD_POWER), now)
        planning_base = number(data.get("rolling_planning_base_load_w"))
        loads = [
            value
            for value in (configured_base, planning_base)
            if value is not None and value >= 0
        ]
        if solar is None or not loads:
            return None
        return max(solar - max(loads), 0.0)

    def _update_counterfactual_ledger(self, data, now):
        capacity = number(self.cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        stored = number(data.get("stored_energy"))
        limit = _num(self.hass, self.cfg.get(CONF_CHARGE_LIMIT))
        if capacity is None or capacity <= 0 or stored is None or limit is None:
            data.update(
                counterfactual_status="unavailable",
                counterfactual_reason="Battery energy or charge-limit input unavailable",
            )
            return

        efficiency = float(
            self.cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)
        )
        ev_power = number(data.get("rolling_ev_current_power_w")) or 0.0
        solar_surplus = self._live_solar_surplus_w(data, now)
        ledger = advance_counterfactual_ledger(
            self._trust.get("counterfactual_ledger"),
            now=now,
            actual_stored_kwh=stored,
            capacity_kwh=capacity,
            charge_limit_pct=limit,
            charge_efficiency=efficiency,
            ev_power_w=ev_power,
            solar_surplus_w=solar_surplus,
        )
        self._trust["counterfactual_ledger"] = ledger

        ceiling = capacity * min(max(float(limit), 0.0), 100.0) / 100.0
        cf_stored = number(ledger.get("counterfactual_stored_kwh")) or stored
        preserved = max(cf_stored - stored, 0.0)
        wall = number(ledger.get("ev_wall_kwh")) or 0.0
        solar_ev = number(ledger.get("ev_solar_kwh")) or 0.0
        data.update(
            counterfactual_status="tracking",
            counterfactual_reason="Observed battery movement plus measured solar diverted to EV",
            counterfactual_stored_kwh=cf_stored,
            counterfactual_soc_pct=100.0 * cf_stored / capacity,
            counterfactual_headroom_kwh=max(ceiling - cf_stored, 0.0),
            counterfactual_preserved_headroom_kwh=preserved,
            counterfactual_avoided_export_kwh=number(
                ledger.get("avoided_export_kwh")
            )
            or 0.0,
            ev_wall_energy_today_kwh=wall,
            ev_solar_energy_today_kwh=solar_ev,
            ev_solar_fraction_today_pct=(
                100.0 * solar_ev / wall if wall > 0 else None
            ),
            ev_solar_stored_equivalent_today_kwh=number(
                ledger.get("ev_solar_stored_equiv_kwh")
            )
            or 0.0,
            live_solar_surplus_w=solar_surplus,
        )

    def _live_capture_ev(self, data, now):
        data.update(
            live_solar_capture_opportunity=False,
            live_solar_capture_status="clear",
            live_solar_capture_reason="No measured no-action saturation risk.",
            live_solar_capture_recommended_energy_kwh=0.0,
        )
        if data.get("storm") is not False:
            self._live_capture_since = None
            self._last_live_capture_at = now
            data.update(
                live_solar_capture_status="blocked",
                live_solar_capture_reason="Storm safety is active or unavailable.",
            )
            return

        headroom = number(data.get("counterfactual_headroom_kwh"))
        surplus = self._live_solar_surplus_w(data, now)
        _sunrise, sunset = _solar_window(self.hass, now.date())
        if headroom is None or surplus is None or sunset is None or now >= sunset:
            self._live_capture_since = None
            self._last_live_capture_at = now
            return

        remaining_h = max((sunset - now).total_seconds() / 3600.0, 0.0)
        efficiency = float(
            self.cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)
        )
        metrics = live_capture_metrics(
            counterfactual_headroom_kwh=headroom,
            solar_surplus_w=surplus,
            remaining_daylight_hours=remaining_h,
            charge_efficiency=efficiency,
        )
        fill_hours = number(metrics.get("fill_hours"))
        forecast_export = max(
            number(data.get("counterfactual_projected_export_kwh")) or 0.0,
            0.0,
        )
        forecast_risk = forecast_export >= 0.25
        runway_risk = bool(metrics.get("risk"))
        risk = forecast_risk or runway_risk or headroom <= 0.25
        data.update(
            counterfactual_risk_today=risk,
            counterfactual_risk_source=(
                "forecast_and_live_runway"
                if forecast_risk and runway_risk
                else "forecast"
                if forecast_risk
                else "live_runway"
                if runway_risk
                else "none"
            ),
            counterfactual_live_fill_hours=(
                fill_hours if fill_hours is not None and fill_hours < 1_000_000 else None
            ),
            counterfactual_remaining_daylight_hours=remaining_h,
            counterfactual_live_implied_export_kwh=max(
                number(metrics.get("implied_excess_ac_kwh")) or 0.0,
                0.0,
            ),
            live_solar_surplus_w=surplus,
        )
        if not risk:
            self._live_capture_since = None
            self._last_live_capture_at = now
            return

        available = number(data.get("rolling_ev_available_energy_kwh"))
        learned_power = number(data.get("rolling_ev_charge_power_w"))
        current_power = number(data.get("rolling_ev_current_power_w")) or 0.0
        charge_power = current_power if current_power >= 500 else learned_power
        home = self.hass.states.get(self.cfg.get(CONF_EV_HOME, ""))
        soc_status = data.get("rolling_ev_soc_data_status")
        soc_ok = soc_status == "fresh" or (
            current_power >= 500 and soc_status == "charging_soc_stale"
        )
        if home is None or home.state != "home":
            blocked_reason = (
                "No-action saturation risk is present, but the Lexus is not "
                "confirmed home."
            )
        elif available is not None and available <= 0.0:
            blocked_reason = (
                "No-action saturation risk remains, but the Lexus is full; use "
                "another flexible load if measured solar surplus continues."
            )
        elif not soc_ok or available is None:
            blocked_reason = (
                "No-action saturation risk is present, but Lexus SOC/capacity "
                "telemetry is not ready."
            )
        elif charge_power is None or charge_power < 500.0:
            blocked_reason = (
                "No-action saturation risk is present, but learned EV charge power "
                "is unavailable."
            )
        else:
            blocked_reason = None

        if blocked_reason is not None:
            self._live_capture_since = None
            self._last_live_capture_at = now
            data.update(
                live_solar_capture_status="blocked",
                live_solar_capture_reason=blocked_reason,
            )
            return

        live_ok = surplus >= charge_power + 500.0
        gap = (
            self._last_live_capture_at is not None
            and (now - self._last_live_capture_at).total_seconds() > 120
        )
        if not live_ok or gap:
            self._live_capture_since = None
        self._last_live_capture_at = now
        if live_ok:
            self._live_capture_since = self._live_capture_since or now

        implied_export = max(
            number(metrics.get("implied_excess_ac_kwh")) or 0.0,
            forecast_export,
        )
        recommended = min(max(available, 0.0), max(implied_export, 0.0))
        verified = (
            self._live_capture_since is not None
            and (now - self._live_capture_since).total_seconds() >= 600
        )
        if not verified:
            data.update(
                live_solar_capture_status="pending" if live_ok else "waiting_for_surplus",
                live_solar_capture_reason=(
                    "No-action battery saturation risk detected; verifying ten "
                    "minutes of measured solar surplus sufficient for the EV."
                    if live_ok
                    else "No-action battery saturation risk detected; wait for "
                    "measured solar surplus to cover EV power plus 500 W."
                ),
                live_solar_capture_recommended_energy_kwh=recommended,
            )
            return

        reason = (
            "Charge EV now from measured solar: the no-action battery trajectory "
            "would exhaust headroom before sunset."
        )
        data.update(
            live_solar_capture_opportunity=True,
            live_solar_capture_status="capture_now",
            live_solar_capture_reason=reason,
            live_solar_capture_recommended_energy_kwh=recommended,
            today_strategy="capture_solar",
            today_strategy_reason=reason,
            ev_plan=reason,
        )

        # Preserve the user's legacy advisory toggle for the automation-facing
        # eligibility surface. The new live-capture sensor remains informational
        # even when that older toggle is disabled.
        enabled = self.cfg.get(
            OPT_EV_SOLAR_ADVISORY_ENABLED,
            DEFAULT_EV_SOLAR_ADVISORY_ENABLED,
        )
        if enabled:
            data.update(
                rolling_ev_status="green",
                rolling_ev_status_reason=reason,
                rolling_ev_auto_charge_eligible=True,
                rolling_ev_auto_charge_reason=reason,
                rolling_ev_recommended_energy_kwh=recommended,
                rolling_ev_window_start=now.isoformat(),
                rolling_ev_window_end=sunset.isoformat(),
                rolling_ev_window_solar_kwh=recommended,
                rolling_ev_window_grid_kwh=0.0,
                rolling_ev_window_solar_fraction_pct=100.0,
                rolling_ev_headroom_preserved_kwh=recommended * efficiency,
            )

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
        # The point forecast and risk forecast have different jobs.
        #
        # nominal: best estimate shown to the user.
        # energy_security: low-solar/high-load bound used only for the lower SOC
        #   envelope and reserve visibility.
        # export_defense: high-solar/low-load bound used for the zero-export
        #   objective. It must never be replaced by the energy-security case.
        lower_points = [IntervalPoint(p.at, p.watts * .70) for p in points]
        upper_points = [IntervalPoint(p.at, p.watts * 1.30) for p in points]
        common = dict(
            reference=now,
            daylight_windows=windows,
            initial_bank_socs_pct=socs,
            bank_capacities_kwh=capacities,
            charge_limit_pct=limit,
            reserve_pct=reserve,
            charge_efficiency=efficiency,
            controller=controller,
            step_minutes=5,
        )
        nominal = simulate_rolling_days(
            points=points,
            average_load_kw=load / 1000,
            overnight_drop_kw=median_drop,
            **common,
        )

        # Stress each target day from that day's NOMINAL starting battery state.
        # Do not compound "30% more solar / 30% less load" across every prior day;
        # doing so manufactures distant saturation risk even after the point
        # forecast and weather have moved materially lower.
        window_by_date = {window.day: window for window in windows}
        energy_security = []
        export_defense = []
        for mid in nominal:
            window = window_by_date[mid.day]
            stress_common = dict(
                reference=mid.start,
                daylight_windows=[window],
                initial_bank_socs_pct=mid.starting_bank_socs_pct,
                bank_capacities_kwh=capacities,
                charge_limit_pct=limit,
                reserve_pct=reserve,
                charge_efficiency=efficiency,
                controller=controller,
                step_minutes=5,
            )
            low_day = simulate_rolling_days(
                points=lower_points,
                average_load_kw=load / 1000 * 1.30,
                overnight_drop_kw=0.0,
                **stress_common,
            )
            high_day = simulate_rolling_days(
                points=upper_points,
                average_load_kw=load / 1000 * .70,
                overnight_drop_kw=0.0,
                **stress_common,
            )
            if len(low_day) != 1 or len(high_day) != 1:
                raise ValueError("Incomplete per-day stress scenario")
            energy_security.append(low_day[0])
            export_defense.append(high_day[0])

        counterfactual = []
        counterfactual_stored = number(data.get("counterfactual_stored_kwh"))
        if counterfactual_stored is not None:
            counterfactual_socs = counterfactual_bank_socs(
                actual_socs_pct=socs,
                capacities_kwh=capacities,
                target_stored_kwh=counterfactual_stored,
                charge_limit_pct=limit,
            )
            counterfactual = simulate_rolling_days(
                points=points,
                average_load_kw=load / 1000,
                overnight_drop_kw=median_drop,
                **{**common, "initial_bank_socs_pct": counterfactual_socs},
            )

        if (
            len(nominal) != len(rows)
            or len(energy_security) != len(rows)
            or len(export_defense) != len(rows)
        ):
            raise ValueError("Incomplete scenario horizon")
        candidate = None
        amount = 0.0
        export_risk_days = []
        data.update(
            forecast_export_risk=False,
            forecast_export_risk_date="none",
            forecast_export_headroom_kwh=0.0,
            forecast_export_wall_energy_kwh=0.0,
            forecast_export_risk_reason="clear",
            forecast_export_risk_days=[],
            forecast_export_model="export_defense_v2",
            forecast_objective="zero_export",
        )
        reserve_floor = min(max(float(reserve), 0.0), 100.0)
        current_soc = sum(soc * cap for soc, cap in zip(socs, capacities)) / capacity
        for index, (row, mid, lo, hi) in enumerate(
            zip(rows, nominal, energy_security, export_defense)
        ):
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
            export_bias = float(
                profile.get("export_underprediction_bias_soc") or 0.0
            )
            effective_export_bias = (
                export_bias * remaining_fraction if is_today else export_bias
            )
            export_assessment = assess_export_defense(
                nominal=mid,
                defense=hi,
                capacity_kwh=capacity,
                charge_limit_pct=limit,
                forecast_underprediction_soc=effective_export_bias,
                charge_efficiency=efficiency,
            )
            cf_mid = counterfactual[index] if index < len(counterfactual) else None
            if is_today and cf_mid is not None:
                data.update(
                    counterfactual_projected_sunset_soc_pct=round(
                        cf_mid.end_soc_pct, 2
                    ),
                    counterfactual_projected_export_kwh=round(
                        cf_mid.export_kwh, 3
                    ),
                    counterfactual_projected_capacity_export_kwh=round(
                        cf_mid.capacity_export_kwh, 3
                    ),
                    counterfactual_projection_model="no_discretionary_load_live_anchored",
                )

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
                "historical_mae_soc": profile["mae_soc"],
                "historical_signed_bias_soc": profile.get("signed_bias_soc"),
                "export_underprediction_bias_soc": profile.get(
                    "export_underprediction_bias_soc"
                ),
                "energy_security_start_soc_pct": round(lo.start_soc_pct, 2),
                "energy_security_sunset_soc_pct": round(lo.end_soc_pct, 2),
                "export_defense_start_soc_pct": round(hi.start_soc_pct, 2),
                "export_defense_sunset_soc_pct": round(hi.end_soc_pct, 2),
                "export_defense_export_kwh": round(hi.capacity_export_kwh, 3),
                "export_defense_direct_headroom_kwh": round(
                    export_assessment.direct_headroom_kwh, 3
                ),
                "export_defense_uncertainty_headroom_kwh": round(
                    export_assessment.uncertainty_headroom_kwh, 3
                ),
                "export_defense_headroom_kwh": round(
                    export_assessment.headroom_kwh, 3
                ),
                "export_defense_wall_energy_kwh": round(
                    export_assessment.headroom_kwh / max(efficiency, 0.01), 3
                ),
                "export_defense_risk": export_assessment.risk,
                "export_defense_risk_reason": export_assessment.reason,
                "export_defense_risk_adjusted_ceiling_pct": round(
                    export_assessment.risk_adjusted_ceiling_pct, 1
                ),
                # Compatibility aliases. These are no longer derived by
                # subtracting a low-solar "safety margin".
                "safety_margin_kwh": round(
                    export_assessment.uncertainty_headroom_kwh, 2
                ),
                "conservative_headroom_kwh": round(
                    export_assessment.headroom_kwh, 2
                ),
                "forecast_objective": "zero_export",
                "range_kind": "scenario_envelope_not_probability_interval",
                "range_assumption": "per_day_energy_security_low_export_defense_high",
                "counterfactual_sunset_soc_pct": (
                    round(cf_mid.end_soc_pct, 2) if cf_mid is not None else None
                ),
                "counterfactual_export_kwh": (
                    round(cf_mid.export_kwh, 3)
                    if cf_mid is not None
                    else None
                ),
            })
            lead = (lo.day - now.date()).days
            if export_assessment.risk:
                export_risk_days.append(row["date"])
            if candidate is None and export_assessment.risk and lead <= 2:
                candidate = row["date"]
                amount = export_assessment.headroom_kwh
                data.update(
                    forecast_export_risk=True,
                    forecast_export_risk_date=candidate,
                    forecast_export_headroom_kwh=round(amount, 3),
                    forecast_export_wall_energy_kwh=round(
                        amount / max(efficiency, 0.01), 3
                    ),
                    forecast_export_risk_reason=export_assessment.reason,
                )
        data["forecast_export_risk_days"] = export_risk_days
        # EV-window forecasting uses the same low-load assumption as export
        # defense. Live measured surplus still gates an immediate recommendation.
        return (
            profiles,
            candidate,
            amount,
            upper_points,
            windows,
            load * 0.70,
            efficiency,
        )

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
            for key, default in (
                ("records", []),
                ("pending", {}),
                ("revisions", []),
                ("decisions", []),
                ("counterfactual_ledger", None),
            ):
                self._trust.setdefault(key, default)
            # Require new confirmations after restart; keep scored forecasts.
            self._trust["revisions"] = []
        previous_trust = deepcopy(self._trust)
        self._update_counterfactual_ledger(data, now)
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
                            authoritative_headroom_action="Export-defense headroom risk confirmed. EV charging still requires a verified solar window.")
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
        try:
            self._live_capture_ev(data, now)
        except Exception as err:
            _LOGGER.exception("Live solar-capture evaluation failed")
            data.update(
                live_solar_capture_opportunity=False,
                live_solar_capture_status="unavailable",
                live_solar_capture_reason=f"Live capture evaluation unavailable: {type(err).__name__}",
            )
            self._live_capture_since = None

        decision = {"status": status, "reason": reason,
                    "ev_reason": data.get("rolling_ev_auto_charge_reason"),
                    "confidence": data.get("forecast_confidence"),
                    "planning_load_w": data.get("rolling_planning_base_load_w"),
                    "planning_load_source": data.get("rolling_planning_base_load_source"),
                    "storm": data.get("storm"),
                    "revision": data.get("forecast_revision_at"),
                    "counterfactual_risk": data.get("counterfactual_risk_today"),
                    "live_capture": data.get("live_solar_capture_opportunity")}
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
        # Forecast the best solar-rich window on the risk day. For today's
        # window, live measured surplus still gates the immediate recommendation.
        energy = min(available, amount / efficiency)
        risk_window = next(w for w in windows if w.day.isoformat() == candidate)
        if candidate == now.date().isoformat():
            # For a current-day actionable window, keep the target anchored to
            # NOW so live verification cannot chase a moving later optimum.
            earliest = now
            latest = min(
                risk_window.sunset,
                now + timedelta(hours=energy / (power / 1000)),
            )
        else:
            earliest = max(now, risk_window.sunrise)
            latest = risk_window.sunset
        window = choose_ev_charge_window(points=points, daylight_windows=windows,
            earliest=earliest, latest=latest, base_load_kw=load / 1000,
            charge_power_w=power, energy_kwh=energy,
            charge_efficiency=efficiency, step_minutes=15)
        if window is None or window.solar_energy_kwh / max(window.requested_energy_kwh, .001) < .90:
            data["rolling_ev_auto_charge_reason"] = "No export-defense charging window is at least 90% solar supplied."
            self._surplus_since = None
            return
        data.update(
            rolling_ev_recommended_energy_kwh=window.requested_energy_kwh,
            rolling_ev_window_start=window.start.isoformat(),
            rolling_ev_window_end=window.end.isoformat(),
            rolling_ev_window_solar_kwh=window.solar_energy_kwh,
            rolling_ev_window_grid_kwh=window.grid_energy_kwh,
            rolling_ev_window_solar_fraction_pct=(
                100 * window.solar_energy_kwh / window.requested_energy_kwh
            ),
            rolling_ev_headroom_preserved_kwh=window.preserved_stationary_headroom_kwh,
        )
        if window.start > now:
            data.update(
                rolling_ev_status="planned",
                rolling_ev_status_reason=(
                    "Export-defense headroom risk has a forecast solar-rich EV window."
                ),
                rolling_ev_auto_charge_reason=(
                    "Planned solar window; wait for the window and live surplus verification."
                ),
            )
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
            data["rolling_ev_auto_charge_reason"] = "Wait: charging needs an active export-defense window and ten minutes of measured surplus covering EV power plus 500 W."
            return
        reason = "Charge within the verified window: export-defense headroom need and sustained measured solar surplus confirmed."
        data.update(rolling_ev_status="green", rolling_ev_status_reason=reason,
                    rolling_ev_auto_charge_eligible=True, rolling_ev_auto_charge_reason=reason,
                    rolling_ev_recommended_energy_kwh=window.requested_energy_kwh,
                    rolling_ev_window_start=window.start.isoformat(), rolling_ev_window_end=window.end.isoformat(),
                    rolling_ev_window_solar_kwh=window.solar_energy_kwh,
                    rolling_ev_window_grid_kwh=window.grid_energy_kwh,
                    rolling_ev_window_solar_fraction_pct=100 * window.solar_energy_kwh / window.requested_energy_kwh,
                    rolling_ev_headroom_preserved_kwh=window.preserved_stationary_headroom_kwh,
                    ev_plan=reason)
