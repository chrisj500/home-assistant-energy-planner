"""Exercise the real coordinator class with HA I/O replaced by small fakes.

The source class is compiled alone because CI unit tests do not install HA.
Physical simulation, scenario construction, scoring and decision policy are real.
"""
import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone, date
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(ROOT))
import const
from forecast_solar_shadow import IntervalPoint, interval_points_from_payload
from headroom import correct_current_day_points
from counterfactual import (
    advance_counterfactual_ledger,
    counterfactual_bank_socs,
    live_capture_metrics,
)
from rolling_ev import DaylightWindow, simulate_rolling_days, choose_ev_charge_window
from simulation import ControllerSettings
from reliability import MIN_EVIDENCE_SAMPLES, evidence, gate, number, observe, suppress_actions, sunset_envelope


class Parent:
    async def _async_update_data(self):
        return self.baseline


def solar_window(hass, day):
    return (datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=6),
            datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=18))


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 18, 9, tzinfo=timezone.utc)
        namespace = {**globals(), **vars(const), "EnergyPlannerV022Coordinator": Parent,
                     "dt_util": SimpleNamespace(now=lambda: self.now, parse_date=date.fromisoformat),
                     "_solar_window": solar_window, "_LOGGER": logging.getLogger("test.reliability"),
                     "_parse_weights": lambda value: tuple(float(v) for v in value.split(",")),
                     "_controller_settings": lambda *args: ControllerSettings(enabled=True),
                     "_num": lambda hass, key: number(hass.states.get(key).state) if hass.states.get(key) else None}
        tree = ast.parse((ROOT / "v025_coordinator.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(ROOT / "v025_coordinator.py"), "exec"), namespace)
        coordinator_type = namespace["EnergyPlannerV025Coordinator"]
        self.c = coordinator_type.__new__(coordinator_type)
        self.c.cfg = {const.CONF_SOC_1: "s1", const.CONF_SOC_2: "s2", const.CONF_SOC_3: "s3",
                      const.CONF_CHARGE_LIMIT: "limit", const.CONF_SOLAR_REMAINING: "remaining",
                      const.CONF_ACTUAL_SOLAR_POWER: "solar", const.CONF_BASE_LOAD_POWER: "load",
                      const.CONF_EV_HOME: "home", const.OPT_EV_SOLAR_ADVISORY_ENABLED: True}
        self.states = {}
        for key, value in (("s1", 80), ("s2", 80), ("s3", 80), ("limit", 100),
                           ("remaining", 80), ("solar", 15000), ("load", 1000), ("home", "home")):
            self.states[key] = SimpleNamespace(state=str(value), last_updated=self.now,
                                               last_reported=self.now, attributes={"unit_of_measurement": "W"})
        self.c.hass = SimpleNamespace(states=self.states)
        self.c._trust = {"records": [], "pending": {}, "revisions": [], "decisions": []}
        self.c._calibration_data = {"overnight_records": [{"drop_rate_kw": 1}] * 3}
        self.c._surplus_since = None
        self.c._last_live_at = None
        self.c._live_capture_since = None
        self.c._last_live_capture_at = None
        self.c._estimate_last_success = self.now
        async def save(data):
            self.saved = deepcopy(data)
        self.c._trust_store = SimpleNamespace(async_save=save)
        self.c._estimate_payload = {"result": {"watts": {
            (self.now + timedelta(hours=i)).isoformat(): 15000 for i in range(10)}}}
        self.data = {"rolling_day_plans": [{"date": "2026-09-18", "sunset_soc_pct": 100,
                                           "solar_kwh": 80, "dynamic_load_needed": True}],
                     "rolling_planning_base_load_w": 1000, "effective_reserve_floor": 10,
                     "calibration_overnight_median_kw": 1, "weighted_soc": 80,
                     "stored_energy": 39.3216, "storm": False,
                     "rolling_ev_charge_power_w": 6000, "rolling_ev_current_power_w": 0,
                     "rolling_ev_available_energy_kwh": 6,
                     "rolling_ev_soc_data_status": "fresh"}

    def test_real_scenarios_bound_point_and_apply_margin(self):
        profiles, candidate, amount, *_ = self.c._scenarios(self.data, self.now)
        row = self.data["rolling_day_plans"][0]
        self.assertLessEqual(row["sunset_soc_low_pct"], 100)
        self.assertGreaterEqual(row["sunset_soc_high_pct"], 100)
        self.assertEqual(profiles["2026-09-18"]["confidence"], "learning")
        self.assertIn("display_sunset_soc_pct", row)
        self.assertIsNone(row["display_uncertainty_pct"])
        self.assertEqual(row["display_confidence"], "learning")
        self.assertEqual(row["display_forecast_source"], "live_anchored_interval_simulation")
        self.assertGreater(row["safety_margin_kwh"], 0)
        self.assertTrue(row["export_defense_risk"])
        self.assertGreater(row["export_defense_headroom_kwh"], 0)
        self.assertEqual(candidate, "2026-09-18")
        self.assertGreater(amount, 0)

    def test_sunset_scenario_never_crosses_grid_connected_reserve(self):
        self.data["effective_reserve_floor"] = 25
        self.data["rolling_day_plans"][0]["sunset_soc_pct"] = 27
        self.states["s1"].state = "25"
        self.states["s2"].state = "25"
        self.states["s3"].state = "25"
        self.c._scenarios(self.data, self.now)
        row = self.data["rolling_day_plans"][0]
        self.assertGreaterEqual(row["sunset_soc_low_pct"], 25)
        self.assertGreaterEqual(row["sunset_soc_high_pct"], 25)
        self.assertEqual(
            row["range_assumption"],
            "energy_security_low_export_defense_high",
        )

    def test_afternoon_range_uses_current_soc_and_remaining_daylight(self):
        afternoon = self.now.replace(hour=15)
        for key in ("s1", "s2", "s3"):
            self.states[key].state = "24.25"
        self.data["rolling_day_plans"][0]["sunset_soc_pct"] = 32.05
        self.c._scenarios(self.data, afternoon)
        row = self.data["rolling_day_plans"][0]
        self.assertGreaterEqual(row["sunset_soc_low_pct"], 24.25)
        self.assertEqual(row["range_remaining_daylight_fraction"], 0.25)

    def test_display_best_guess_responds_to_live_solar(self):
        for key in ("s1", "s2", "s3"):
            self.states[key].state = "20"
        self.c._estimate_payload["result"]["watts"] = {
            (self.now + timedelta(hours=i)).isoformat(): 3000
            for i in range(10)
        }
        baseline = deepcopy(self.data)
        baseline["rolling_day_plans"][0]["sunset_soc_pct"] = 40

        self.states["solar"].state = "3000"
        self.c._scenarios(baseline, self.now)
        provider_like = baseline["rolling_day_plans"][0]["display_sunset_soc_pct"]

        stronger = deepcopy(self.data)
        stronger["rolling_day_plans"][0]["sunset_soc_pct"] = 40
        self.states["solar"].state = "10000"
        self.c._scenarios(stronger, self.now)
        live_anchored = stronger["rolling_day_plans"][0]["display_sunset_soc_pct"]

        self.assertGreater(live_anchored, provider_like)

    def test_display_uncertainty_contracts_with_remaining_daylight(self):
        self.c._trust["records"] = [
            {"lead": 0, "error_soc": error}
            for error in (5, -6, 7)
        ]
        self.c._scenarios(self.data, self.now)
        row = self.data["rolling_day_plans"][0]
        # 09:00 is 75% of the 06:00-18:00 daylight window remaining.
        self.assertEqual(row["display_uncertainty_pct"], 4.5)
        self.assertEqual(row["display_confidence"], "medium")

        afternoon = self.now.replace(hour=15)
        later = deepcopy(self.data)
        self.c._scenarios(later, afternoon)
        later_row = later["rolling_day_plans"][0]
        self.assertEqual(later_row["range_remaining_daylight_fraction"], 0.25)
        self.assertEqual(later_row["display_uncertainty_pct"], 1.5)

    def test_global_instability_does_not_downgrade_live_today_display(self):
        self.c._trust["records"] = [
            {"lead": 0, "error_soc": error}
            for error in (5, -6, 7)
        ]
        self.c._trust["revisions"] = [{
            "at": (self.now - timedelta(minutes=10)).isoformat(),
            "revision": "older-provider-refresh",
            "targets": {
                "2026-09-18": {"soc": 20.0, "solar": 10.0},
            },
            "candidate": "2026-09-18",
        }]
        self.c.baseline = deepcopy(self.data)
        result = asyncio.run(self.c._async_update_data())
        row = result["rolling_day_plans"][0]
        self.assertEqual(result["forecast_reliability_status"], "unstable")
        self.assertEqual(row["confidence"], "low")
        self.assertEqual(row["display_confidence"], "medium")

    def test_export_risk_uses_high_solar_case_not_low_solar_case(self):
        # Nominal production is moderate, but the export-defense envelope should
        # still protect headroom if solar materially beats the point forecast.
        for key in ("s1", "s2", "s3"):
            self.states[key].state = "82"
        self.c._estimate_payload["result"]["watts"] = {
            (self.now + timedelta(hours=i)).isoformat(): 7000
            for i in range(10)
        }
        self.data["rolling_day_plans"][0]["sunset_soc_pct"] = 95
        profiles, candidate, amount, *_ = self.c._scenarios(self.data, self.now)
        row = self.data["rolling_day_plans"][0]
        self.assertEqual(row["forecast_objective"], "zero_export")
        self.assertTrue(row["export_defense_risk"])
        self.assertGreater(row["export_defense_sunset_soc_pct"], 90)
        self.assertGreater(row["export_defense_headroom_kwh"], 0)
        self.assertEqual(candidate, "2026-09-18")
        self.assertGreater(amount, 0)
        self.assertTrue(self.data["forecast_export_risk"])
        self.assertEqual(
            self.data["forecast_export_risk_date"],
            "2026-09-18",
        )

    def test_export_risk_is_visible_before_action_gate_is_ready(self):
        self.c.baseline = deepcopy(self.data)
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["forecast_reliability_status"], "learning")
        self.assertTrue(result["forecast_export_risk"])
        self.assertNotEqual(result["forecast_export_risk_date"], "none")
        self.assertGreater(result["forecast_export_headroom_kwh"], 0)
        self.assertFalse(result["rolling_ev_auto_charge_eligible"])

    def test_low_solar_does_not_justify_headroom(self):
        self.states["remaining"].state = "1"
        self.states["solar"].state = "200"
        self.c._estimate_payload["result"]["watts"] = {k: 200 for k in self.c._estimate_payload["result"]["watts"]}
        _, candidate, amount, *_ = self.c._scenarios(self.data, self.now)
        self.assertIsNone(candidate)
        self.assertEqual(amount, 0)

    def test_unknown_soc_blocks_but_unchanged_numeric_soc_remains_valid(self):
        self.states["s1"].state = "nan"
        with self.assertRaises(ValueError):
            self.c._scenarios(self.data, self.now)

        self.states["s1"].state = "80"
        self.states["s1"].last_reported -= timedelta(hours=1)
        profiles, candidate, amount, *_ = self.c._scenarios(self.data, self.now)
        self.assertIn("2026-09-18", profiles)
        self.assertEqual(candidate, "2026-09-18")
        self.assertGreater(amount, 0)

    def test_snapshot_is_fixed_and_settled_once(self):
        self.c._score_forecasts(self.data, self.now)
        self.data["rolling_day_plans"][0]["sunset_soc_pct"] = 20
        self.c._score_forecasts(self.data, self.now + timedelta(hours=1))
        self.data["weighted_soc"] = 30
        sunset = self.now.replace(hour=18)
        self.c._score_forecasts(self.data, sunset)
        self.c._score_forecasts(self.data, sunset + timedelta(minutes=1))
        records = self.c._trust["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["predicted_soc"], 100)
        self.assertEqual(records[0]["error_soc"], -70)

    def test_missed_sunset_is_not_scored_next_morning(self):
        self.c._score_forecasts(self.data, self.now)
        self.c._score_forecasts({"weighted_soc": 10}, self.now + timedelta(days=1))
        self.assertEqual(self.c._trust["records"], [])

    def test_cold_start_suppresses_old_green_advice(self):
        self.c.baseline = {**self.data, "rolling_ev_status": "green", "headroom_release": True}
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["forecast_reliability_status"], "learning")
        self.assertFalse(result["headroom_release"])
        self.assertFalse(result["rolling_ev_auto_charge_eligible"])

    def test_failure_suppresses_all_outputs(self):
        self.c.baseline = {**self.data, "headroom_release": True}
        self.c._estimate_payload = None
        with self.assertLogs("test.reliability", level="ERROR"):
            result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["forecast_reliability_status"], "unavailable")
        self.assertFalse(result["headroom_release"])

    def test_full_update_confirms_risk_but_does_not_skip_live_ev_check(self):
        self.c._trust["records"] = [{"lead": 0, "error_soc": 1}] * 3
        baseline = deepcopy(self.data)
        for minute in (0, 30, 60):
            self.now = datetime(2026, 9, 18, 9, tzinfo=timezone.utc) + timedelta(minutes=minute)
            self.c._estimate_last_success = self.now
            for state in self.states.values():
                state.last_reported = self.now
            self.c.baseline = deepcopy(baseline)
            result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["forecast_reliability_status"], "ready")
        self.assertTrue(result["authoritative_headroom_risk"])
        self.assertEqual(result["rolling_dynamic_load_days_count"], 1)
        self.assertFalse(result["rolling_ev_auto_charge_eligible"])
        self.assertFalse(result["headroom_release"])

    def test_live_capture_can_override_forecast_learning_for_no_regret_ev_use(self):
        baseline = deepcopy(self.data)
        result = None
        for minute in range(11):
            self.now = datetime(2026, 9, 18, 9, tzinfo=timezone.utc) + timedelta(minutes=minute)
            self.c._estimate_last_success = self.now
            for state in self.states.values():
                state.last_reported = self.now
                state.last_updated = self.now
            self.c.baseline = deepcopy(baseline)
            result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["forecast_reliability_status"], "learning")
        self.assertTrue(result["counterfactual_risk_today"])
        self.assertTrue(result["live_solar_capture_opportunity"])
        self.assertEqual(result["live_solar_capture_status"], "capture_now")
        self.assertGreater(result["live_solar_capture_recommended_energy_kwh"], 0)
        self.assertEqual(result["today_strategy"], "capture_solar")

    def test_live_capture_does_not_require_legacy_advisory_toggle(self):
        self.c.cfg[const.OPT_EV_SOLAR_ADVISORY_ENABLED] = False
        baseline = deepcopy(self.data)
        result = None
        for minute in range(11):
            self.now = datetime(2026, 9, 18, 9, tzinfo=timezone.utc) + timedelta(minutes=minute)
            self.c._estimate_last_success = self.now
            for state in self.states.values():
                state.last_reported = self.now
                state.last_updated = self.now
            self.c.baseline = deepcopy(baseline)
            result = asyncio.run(self.c._async_update_data())
        self.assertTrue(result["live_solar_capture_opportunity"])
        self.assertFalse(result["rolling_ev_auto_charge_eligible"])

    def test_storm_blocks_live_capture_even_with_solar_surplus(self):
        self.data["storm"] = True
        self.c._update_counterfactual_ledger(self.data, self.now)
        self.c._live_capture_ev(self.data, self.now)
        self.assertFalse(self.data["live_solar_capture_opportunity"])
        self.assertEqual(self.data["live_solar_capture_status"], "blocked")

    def test_learning_progress_reports_both_required_evidence_sets(self):
        self.c._trust["records"] = [{"lead": 0, "error_soc": 1}] * 2
        self.c.baseline = deepcopy(self.data)
        result = asyncio.run(self.c._async_update_data())
        self.assertFalse(result["forecast_learning_ready"])
        self.assertEqual(result["forecast_learning_sunset_samples"], 2)
        self.assertEqual(result["forecast_learning_sunset_required"], 3)
        self.assertEqual(result["forecast_learning_overnight_samples"], 3)
        self.assertEqual(result["forecast_learning_overnight_required"], 3)
        self.assertIn("2/3 scored sunsets", result["forecast_learning_progress"])

    def test_battery_outlook_failure_preserves_stored_learning_progress(self):
        self.c._trust["records"] = [{"lead": 0, "error_soc": 1}] * 2
        self.c._calibration_data = {
            "overnight_records": [{"drop_rate_kw": 1}] * 9
        }
        self.c.baseline = {
            "battery_outlook_status": "unavailable",
            "battery_outlook_reason": "Interval solar forecast unavailable",
        }
        with self.assertLogs("test.reliability", level="ERROR"):
            result = asyncio.run(self.c._async_update_data())

        self.assertEqual(result["forecast_reliability_status"], "unavailable")
        self.assertEqual(result["forecast_learning_sunset_samples"], 2)
        self.assertEqual(result["forecast_learning_overnight_samples"], 9)
        self.assertEqual(
            result["forecast_learning_progress"],
            "2/3 scored sunsets · 9/3 overnight records",
        )
        self.assertIn(
            "Battery outlook unavailable: Interval solar forecast unavailable",
            result["forecast_reliability_reason"],
        )
        self.assertEqual(
            result["forecast_reliability_error"]["battery_outlook_status"],
            "unavailable",
        )

    def test_missing_overnight_evidence_keeps_confidence_learning(self):
        self.c._trust["records"] = [{"lead": 0, "error_soc": 1}] * 10
        self.c._calibration_data = {}
        profiles, *_ = self.c._scenarios(self.data, self.now)
        self.assertEqual(profiles["2026-09-18"]["confidence"], "learning")

    def ev_inputs(self):
        return ([IntervalPoint(self.now, 15000), IntervalPoint(self.now + timedelta(hours=6), 15000)],
                [DaylightWindow(self.now.date(), *solar_window(None, self.now.date()))])

    def verify_ev(self, minute):
        now = self.now + timedelta(minutes=minute)
        for state in self.states.values():
            state.last_reported = now
        suppress_actions(self.data, "ready", "confirmed")
        points, windows = self.ev_inputs()
        self.c._verified_ev(self.data, now, "2026-09-18", 6, points, windows, 1300, .9)

    def test_future_export_risk_can_publish_a_planned_ev_window(self):
        tomorrow = self.now.date() + timedelta(days=1)
        sunrise = self.now.replace(hour=6) + timedelta(days=1)
        sunset = self.now.replace(hour=18) + timedelta(days=1)
        points = [
            IntervalPoint(sunrise, 0),
            IntervalPoint(sunrise + timedelta(hours=6), 15000),
            IntervalPoint(sunset, 0),
        ]
        windows = [DaylightWindow(tomorrow, sunrise, sunset)]
        suppress_actions(self.data, "ready", "confirmed")
        self.data["rolling_ev_available_energy_kwh"] = 6
        self.data["rolling_ev_charge_power_w"] = 6000
        self.data["rolling_ev_soc_data_status"] = "fresh"
        self.c._verified_ev(
            self.data,
            self.now,
            tomorrow.isoformat(),
            4.5,
            points,
            windows,
            700,
            0.9,
        )
        self.assertEqual(self.data["rolling_ev_status"], "planned")
        self.assertFalse(self.data["rolling_ev_auto_charge_eligible"])
        self.assertIsNotNone(self.data["rolling_ev_window_start"])
        self.assertGreater(self.data["rolling_ev_recommended_energy_kwh"], 0)

    def test_ev_needs_ten_minutes_and_revokes_on_solar_loss(self):
        for minute in range(10):
            self.verify_ev(minute)
            self.assertFalse(self.data["rolling_ev_auto_charge_eligible"])
        self.verify_ev(10)
        self.assertTrue(self.data["rolling_ev_auto_charge_eligible"])
        self.states["solar"].state = "2000"
        self.verify_ev(11)
        self.assertFalse(self.data["rolling_ev_auto_charge_eligible"])

    def test_gap_does_not_count_as_sustained_surplus(self):
        self.verify_ev(0)
        self.verify_ev(11)
        self.assertFalse(self.data["rolling_ev_auto_charge_eligible"])

    def test_missing_home_and_stale_ev_soc_block(self):
        self.states.pop("home")
        self.verify_ev(0)
        self.assertFalse(self.data["rolling_ev_auto_charge_eligible"])
        self.states["home"] = SimpleNamespace(state="home")
        self.data["rolling_ev_soc_data_status"] = "stale"
        self.verify_ev(1)
        self.assertFalse(self.data["rolling_ev_auto_charge_eligible"])

    def test_stale_live_power_and_unknown_unit_rejected(self):
        self.assertIsNone(self.c._fresh_power("solar", self.now + timedelta(minutes=6)))
        self.states["solar"].attributes["unit_of_measurement"] = "kWh"
        self.assertIsNone(self.c._fresh_power("solar", self.now))
