from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from reliability import evidence, observe, gate, suppress_actions, number


class ReliabilityTests(unittest.TestCase):
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

    def test_history_is_horizon_specific_and_no_samples_is_not_confident(self):
        records = [{"lead": 0, "error_soc": 1}] * 10
        self.assertEqual(evidence(records, 0)["confidence"], "high")
        self.assertEqual(evidence(records, 4)["confidence"], "learning")
        self.assertIsNone(evidence([], 0)["mae_soc"])
        self.assertEqual(evidence([{"lead": 0, "error_soc": 30}] * 10, 0)["confidence"], "low")

    def test_fail_closed_conditions(self):
        defaults = dict(fresh=True, unstable=False, confidence="high", candidate=self.day, stable=True, storm=False)
        for changes, expected in [({"fresh": False}, "unavailable"), ({"storm": None}, "blocked"),
                                  ({"storm": True}, "blocked"), ({"confidence": "learning"}, "learning"),
                                  ({"candidate": None}, "clear"), ({"stable": False}, "pending")]:
            self.assertEqual(gate(**{**defaults, **changes})[0], expected)

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
