"""Check temporal isolation, fallbacks, freezing, and matched challenger scores."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from forecast_solar_shadow import IntervalPoint
from solar_learning import finalize
from solar_challengers import annotate_latest, blend_prediction, persistence_prediction, scorecard

NOW = datetime(2026, 9, 23, 16, 30, tzinfo=timezone.utc)


def candidate():
    return {"issued_at": NOW.timestamp(), "start": (NOW + timedelta(minutes=30)).timestamp(),
            "end": (NOW + timedelta(minutes=90)).timestamp(), "lead": "0-3h",
            "day": "2026-09-23", "hour": 17, "raw_kwh": 2.0, "live_kwh": 2.0,
            "learned_kwh": 2.0, "v4_learned_kwh": 3.0, "v41_learned_kwh": 2.0,
            "trained": False, "v4_trained": True, "v41_trained": False,
            "observed_at_issue": {"valid": True, "power_w": 4000, "at": NOW.timestamp()}}


def history():
    rows = []
    for day in range(1, 5):
        for hour in range(3):
            row = candidate()
            shift = day * 86400 + hour * 3600
            for key in ("issued_at", "start", "end"):
                row[key] -= shift
            row.update(day=f"2026-09-{23-day}", actual_kwh=3.0, accepted=True)
            rows.append(row)
    return rows


def points():
    return [IntervalPoint(NOW + timedelta(minutes=minutes), 2000) for minutes in (0, 30, 60, 90, 120, 180)]


class BlendTests(unittest.TestCase):
    def test_completed_horizon_evidence_moves_blend_toward_better_member(self):
        value, ready, meta = blend_prediction({"scored": history()}, candidate())
        self.assertTrue(ready)
        self.assertGreater(value, 2)
        self.assertLess(value, 3)
        self.assertEqual(meta["samples"], 12)
        self.assertEqual(meta["days"], 4)
        self.assertAlmostEqual(sum(meta["weights"].values()), 1)
        self.assertGreater(meta["weights"]["live"], 0.5)  # Initial baseline prior.

    def test_future_outcomes_and_other_horizons_cannot_change_weights(self):
        rows = history()
        expected = blend_prediction({"scored": rows}, candidate())
        future = dict(candidate(), accepted=True, actual_kwh=100)
        other = [dict(r, lead="3-12h", actual_kwh=100) for r in rows]
        self.assertEqual(blend_prediction({"scored": rows + other + [future]}, candidate()), expected)

    def test_repeated_issues_do_not_satisfy_evidence_gate(self):
        rows = history()[:1] * 100
        value, ready, meta = blend_prediction({"scored": rows}, candidate())
        self.assertFalse(ready)
        self.assertEqual(value, 2)
        self.assertEqual(meta["samples"], 1)

    def test_missing_member_uses_common_cohort_and_does_not_select_older_issue(self):
        rows = history()
        rows.append(dict(rows[0], issued_at=rows[0]["issued_at"] + 60, v4_learned_kwh=None))
        _, ready, meta = blend_prediction({"scored": rows}, candidate())
        self.assertFalse(ready)
        self.assertEqual(meta["samples"], 11)

    def test_untrained_or_equal_members_do_not_get_duplicate_weight(self):
        row = dict(candidate(), v4_trained=False)
        value, ready, meta = blend_prediction({"scored": history()}, row)
        self.assertEqual(value, 2)
        self.assertFalse(ready)
        self.assertEqual(meta["weights"], {"live": 1.0})

    def test_long_horizon_fallback_is_raw(self):
        row = dict(candidate(), lead="12-24h", live_kwh=7)
        self.assertEqual(blend_prediction({}, row)[0], 2)

    def test_old_and_invalid_outcomes_do_not_train(self):
        for changes in ({"accepted": False}, {"actual_kwh": float("nan")},
                        {"end": NOW.timestamp() - 22 * 86400}):
            _, ready, _ = blend_prediction({"scored": [dict(r, **changes) for r in history()]}, candidate())
            self.assertFalse(ready)


class PersistenceTests(unittest.TestCase):
    def test_live_anchor_adjusts_short_term_only_and_fades(self):
        row = candidate()
        value, active, meta = persistence_prediction(row, points(), 40, -75)
        self.assertTrue(active)
        self.assertGreater(value, 2)
        self.assertLess(value, 4)
        self.assertEqual(meta["shape"], "haurwitz_ghi_proxy")
        row.update(start=NOW.timestamp()+7200, end=NOW.timestamp()+10800)
        self.assertEqual(persistence_prediction(row, points(), 40, -75)[:2], (2, False))

    def test_zero_production_can_reduce_forecast_when_valid(self):
        row = candidate()
        row["observed_at_issue"]["power_w"] = 0
        value, active, _ = persistence_prediction(row, points(), 40, -75)
        self.assertTrue(active)
        self.assertLess(value, 2)
        self.assertGreaterEqual(value, 0)

    def test_stale_future_nonfinite_or_invalid_observation_falls_back(self):
        for changes in ({"valid": False}, {"power_w": float("nan")}, {"power_w": -1},
                        {"at": NOW.timestamp()-301}, {"at": NOW.timestamp()+1}):
            row = candidate()
            row["observed_at_issue"].update(changes)
            self.assertEqual(persistence_prediction(row, points(), 40, -75)[:2], (2, False))

    def test_low_sun_and_missing_geometry_fall_back(self):
        for lat, lon in ((None, -75), (40, 100)):
            self.assertEqual(persistence_prediction(candidate(), points(), lat, lon)[:2], (2, False))

    def test_missing_gapped_or_nonfinite_curve_falls_back(self):
        bad_curves = [[], points()[:2],
                      [IntervalPoint(NOW, 2000), IntervalPoint(NOW+timedelta(hours=3), 2000)],
                      [IntervalPoint(p.at, float("inf")) for p in points()]]
        for curve in bad_curves:
            self.assertEqual(persistence_prediction(candidate(), curve, 40, -75)[:2], (2, False))


class LifecycleTests(unittest.TestCase):
    def test_frozen_predictions_survive_reload_and_changed_truth(self):
        memory = {"scored": history(), "pending": [candidate()]}
        annotate_latest(memory, NOW.timestamp(), points(), 40, -75)
        frozen = deepcopy(memory["pending"])
        memory = deepcopy(memory)  # JSON storage preserves these plain values.
        memory["scored"] = [dict(r, actual_kwh=100) for r in history()]
        annotate_latest(memory, NOW.timestamp(), [], 0, 0)
        self.assertEqual(memory["pending"], frozen)

    def test_finalize_keeps_forecasts_and_scorecard_matches_same_rows(self):
        row = candidate()
        memory = {"scored": history(), "pending": [row],
                  "actual_hours": {str(int(row["start"])): {"kwh": 3, "coverage_seconds": 3600}}}
        annotate_latest(memory, NOW.timestamp(), points(), 40, -75)
        finalize(memory, row["end"])
        stats = scorecard(memory, "persistence")
        self.assertEqual(stats["scored_forecasts"], 1)  # Old history is not scored as a new prediction.
        self.assertEqual(stats["active_forecasts"], 1)
        group = stats["by_horizon"]["0-3h"]["all"]
        for key in ("raw", "live", "v3", "v4", "v41", "blend", "persistence"):
            self.assertEqual(group[key]["samples"], 1)
        self.assertEqual(group["v4"]["mae_kwh"], 0)
        duplicate = dict(memory["scored"][-1], issued_at=NOW.timestamp()-60, persistence_kwh=100)
        memory["scored"].append(duplicate)
        self.assertEqual(scorecard(memory, "persistence"), stats | {
            "by_horizon": stats["by_horizon"] | {"0-3h": stats["by_horizon"]["0-3h"] | {"issued_rows": 2}}
        })

    def test_fallback_rows_excluded_from_active_comparison(self):
        row = dict(candidate(), start=NOW.timestamp()+10800, end=NOW.timestamp()+14400, lead="3-12h")
        memory = {"pending": [row]}
        annotate_latest(memory, NOW.timestamp(), points(), 40, -75)
        memory["scored"] = [dict(row, actual_kwh=2, accepted=True)]
        stats = scorecard(memory, "persistence")
        self.assertEqual(stats["scored_forecasts"], 1)
        self.assertEqual(stats["active_forecasts"], 0)


if __name__ == "__main__":
    unittest.main()
