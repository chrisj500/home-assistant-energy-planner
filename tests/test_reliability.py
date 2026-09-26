from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from reliability import (
    RELIABILITY_RECORD_SCHEMA_VERSION,
    evidence,
    gate,
    migrate_records,
    number,
    observe,
    suppress_actions,
    sunset_envelope,
)


class ReliabilityTests(unittest.TestCase):
    def test_suppression_preserves_nominal_risk_amount(self):
        data = {
            "rolling_day_plans": [{
                "date": "2026-09-26",
                "confidence": "medium",
                "dynamic_load_needed": True,
                "dynamic_load_needed_kwh": 4.25,
            }]
        }
        suppress_actions(data, "clear", "No robust action")
        row = data["rolling_day_plans"][0]
        self.assertTrue(row["nominal_dynamic_load_needed"])
        self.assertEqual(row["nominal_dynamic_load_needed_kwh"], 4.25)
        self.assertFalse(row["dynamic_load_needed"])
        self.assertEqual(row["dynamic_load_needed_kwh"], 0.0)

    def setUp(self):
        self.now = datetime(2026, 9, 18, 9, tzinfo=timezone.utc)
        self.day = "2026-09-19"

    def rows(self, solar=28.4, soc=60):
        return [{"date": self.day, "solar_kwh": solar, "sunset_soc_pct": soc}]

    def observe(self, history, minute, solar=28.4, soc=60, candidate="2026-09-19", revision=None):
        now = self.now + timedelta(minutes=minute)
        return observe(history, now=now, revision=revision or now.isoformat(),
                       rows=self.rows(solar, soc), candidate=candidate)

    def test_september_18_solar_swing_blocks_even_if_soc_saturates(self):
        history = []
        for minute, solar in [(0, 28.4), (30, 75.5), (60, 22.1)]:
            history, unstable, stable, count = self.observe(history, minute, solar, 100)
        self.assertTrue(unstable)
        self.assertEqual(gate(fresh=True, unstable=unstable, confidence="high",
                              candidate=self.day, stable=stable, storm=False)[0], "unstable")

    def test_cache_polls_cannot_confirm_a_recommendation(self):
        history = []
        for minute in range(61):
            history, _, stable, count = self.observe(history, minute, revision="one_fetch")
        self.assertFalse(stable)
        self.assertEqual(count, 1)

    def test_three_refreshes_and_one_hour_required(self):
        history = []
        for minute in (0, 15, 30):
            history, _, stable, _ = self.observe(history, minute)
        self.assertFalse(stable)
        _, _, stable, _ = self.observe(history, 60)
        self.assertTrue(stable)

    def test_reversal_resets_streak(self):
        history = []
        for minute, candidate in [(0, self.day), (30, self.day), (45, None), (60, self.day)]:
            history, _, stable, count = self.observe(history, minute, candidate=candidate)
        self.assertFalse(stable)
        self.assertEqual(count, 1)

    def test_cached_soc_spike_is_remembered(self):
        h, _, _, _ = self.observe([], 0, revision="one")
        h, unstable, _, _ = self.observe(h, 1, soc=90, revision="one")
        self.assertTrue(unstable)
        h, unstable, _, count = self.observe(h, 2, revision="one")
        self.assertTrue(unstable)
        self.assertEqual(count, 1)
        _, unstable, _, _ = self.observe(h, 183, revision="two")
        self.assertFalse(unstable)

    def test_same_day_remaining_energy_decline_is_not_revision(self):
        rows = [{"date": "2026-09-18", "solar_kwh": 40, "sunset_soc_pct": 50}]
        h, _, _, _ = observe([], now=self.now, revision="a", rows=rows, candidate=None)
        rows[0]["solar_kwh"] = 20
        _, unstable, _, _ = observe(h, now=self.now + timedelta(hours=1), revision="b", rows=rows, candidate=None)
        self.assertFalse(unstable)

    def test_legacy_soc_records_migrate_to_physical_energy(self):
        records = [{
            "lead": 0,
            "predicted_soc": 60.0,
            "actual_soc": 70.0,
            "error_soc": 10.0,
        }]
        migrated, info = migrate_records(
            records,
            fallback_capacity_kwh=49.152,
        )
        row = migrated[0]
        self.assertTrue(info["changed"])
        self.assertEqual(info["capacity_inferred_records"], 1)
        self.assertEqual(row["record_schema"], RELIABILITY_RECORD_SCHEMA_VERSION)
        self.assertAlmostEqual(row["capacity_kwh"], 49.152)
        self.assertAlmostEqual(row["predicted_stored_kwh"], 29.4912)
        self.assertAlmostEqual(row["actual_stored_kwh"], 34.4064)
        self.assertAlmostEqual(row["error_kwh"], 4.9152)
        self.assertEqual(
            row["topology_signature"],
            {"capacity_kwh": 49.152, "pack_counts": None},
        )

    def test_physical_error_survives_capacity_increase(self):
        records = [{
            "lead": 0,
            "error_soc": 10.0,
            "error_kwh": 4.9152,
            "capacity_kwh": 49.152,
            "record_schema": RELIABILITY_RECORD_SCHEMA_VERSION,
        }]
        old = evidence(records, 0, capacity_kwh=49.152)
        larger = evidence(records, 0, capacity_kwh=55.296)
        self.assertAlmostEqual(old["mae_soc"], 10.0)
        self.assertAlmostEqual(larger["mae_soc"], 8.8888889, places=5)
        self.assertAlmostEqual(old["mae_kwh"], 4.9152)
        self.assertAlmostEqual(larger["mae_kwh"], 4.9152)
        self.assertAlmostEqual(
            larger["export_underprediction_bias_kwh"],
            4.9152,
        )

    def test_completed_kwh_records_need_no_migration_rewrite(self):
        records = [{
            "lead": 1,
            "error_soc": -5.0,
            "error_kwh": -2.4576,
            "capacity_kwh": 49.152,
            "predicted_stored_kwh": 30.0,
            "actual_stored_kwh": 27.5424,
            "topology_signature": {
                "capacity_kwh": 49.152,
                "pack_counts": [3, 2, 3],
            },
            "record_schema": RELIABILITY_RECORD_SCHEMA_VERSION,
        }]
        migrated, info = migrate_records(
            records,
            fallback_capacity_kwh=55.296,
        )
        self.assertFalse(info["changed"])
        self.assertEqual(migrated, records)

    def test_history_is_horizon_specific_and_no_samples_is_not_confident(self):
        records = [{"lead": 0, "error_soc": 1}] * 10
        self.assertEqual(evidence(records, 0)["confidence"], "high")
        self.assertEqual(evidence(records, 4)["confidence"], "learning")
        self.assertIsNone(evidence([], 0)["mae_soc"])
        self.assertEqual(evidence([{"lead": 0, "error_soc": 30}] * 10, 0)["confidence"], "low")

    def test_export_bias_is_directional_not_absolute(self):
        overpredicted = [
            {"lead": 0, "error_soc": value}
            for value in (-30, -20, -10, -5)
        ]
        profile = evidence(overpredicted, 0)
        self.assertEqual(profile["export_underprediction_bias_soc"], 0.0)
        self.assertLess(profile["signed_bias_soc"], 0)

        underpredicted = [
            {"lead": 0, "error_soc": value}
            for value in (4, 8, -2, 6)
        ]
        profile = evidence(underpredicted, 0)
        self.assertAlmostEqual(profile["signed_bias_soc"], 4.0)
        self.assertAlmostEqual(profile["export_underprediction_bias_soc"], 4.0)

    def test_today_envelope_anchors_to_live_soc_and_converges(self):
        sunrise = self.now.replace(hour=6)
        sunset = self.now.replace(hour=18)
        args = dict(sunrise=sunrise, sunset=sunset, target_date=self.now.date(),
                    current_soc=24.25, point_soc=32.05, low_soc=29,
                    high_soc=35, historical_width=10, reserve_floor=20)
        morning = sunset_envelope(now=self.now, **args)
        late = sunset_envelope(now=self.now.replace(hour=17), **args)
        self.assertGreaterEqual(morning[0], 24.25)
        self.assertGreater(late[0], morning[0])
        self.assertLess(late[1], morning[1])
        self.assertLess(late[2], morning[2])

    def test_future_envelope_preserves_full_uncertainty_and_overnight_drop(self):
        sunrise = self.now.replace(hour=6) + timedelta(days=1)
        sunset = sunrise + timedelta(hours=12)
        low, high, fraction = sunset_envelope(
            now=self.now, sunrise=sunrise, sunset=sunset,
            target_date=sunrise.date(), current_soc=80,
            point_soc=50, low_soc=45, high_soc=55,
            historical_width=13, reserve_floor=20)
        self.assertEqual((low, high, fraction), (37, 63, 1.0))

    def test_fail_closed_conditions(self):
        defaults = dict(fresh=True, unstable=False, confidence="high", candidate=self.day, stable=True, storm=False)
        for changes, expected in [({"fresh": False}, "unavailable"), ({"storm": None}, "storm_sensor_unavailable"),
                                  ({"storm": True}, "storm_active"), ({"confidence": "learning"}, "learning"),
                                  ({"candidate": None}, "clear"), ({"stable": False}, "pending")]:
            self.assertEqual(gate(**{**defaults, **changes})[0], expected)

    def test_storm_reason_exposes_entity_and_raw_state(self):
        status, reason = gate(
            fresh=True,
            unstable=False,
            confidence="high",
            candidate=self.day,
            stable=True,
            storm=None,
            storm_entity="binary_sensor.storm_watch",
            storm_state="unknown",
        )
        self.assertEqual(status, "storm_sensor_unavailable")
        self.assertIn("binary_sensor.storm_watch = unknown", reason)

        status, reason = gate(
            fresh=True,
            unstable=False,
            confidence="high",
            candidate=self.day,
            stable=True,
            storm=True,
            storm_entity="binary_sensor.storm_watch",
            storm_state="on",
        )
        self.assertEqual(status, "storm_active")
        self.assertIn("binary_sensor.storm_watch = on", reason)

    def test_legacy_actions_are_all_closed(self):
        data = {"headroom_release": True, "rolling_ev_auto_charge_eligible": True,
                "rolling_ev_recommended_energy_kwh": 8,
                "rolling_day_plans": [{"dynamic_load_needed": True, "dynamic_load_needed_kwh": 8}]}
        suppress_actions(data, "unstable", "Forecast unstable—do not act.")
        self.assertFalse(data["headroom_release"])
        self.assertFalse(data["rolling_ev_auto_charge_eligible"])
        self.assertEqual(data["rolling_ev_recommended_energy_kwh"], 0)
        self.assertEqual(data["rolling_dynamic_load_days_count"], 0)
        self.assertFalse(data["rolling_day_plans"][0]["dynamic_load_needed"])
        self.assertIsNone(data["rolling_ev_window_start"])

    def test_nonfinite_is_not_valid_telemetry(self):
        for value in ("nan", "inf", None, "unavailable"):
            self.assertIsNone(number(value))
