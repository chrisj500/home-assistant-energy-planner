"""Forecast replay and production validity tests for the AC shadow learner."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from solar_learning import finalize, issue, observe, prediction, scorecard


def candidate(start=864000, issued_at=None, **overrides):
    return {"start": start, "end": start + 3600, "issued_at": start - 3600 if issued_at is None else issued_at,
            "day": str(start // 86400), "hour": 12, "lead": "1-3h", "sky_bin": "clear",
            "raw_kwh": 2.0, "live_kwh": 2.1, **overrides}


def observation(at, power=2000, valid=True, energy=None):
    return {"at": at, "power_w": power, "valid": valid, "energy_kwh": energy}


class SolarLearningTests(unittest.TestCase):
    def test_full_hour_integrates_ac_energy(self):
        memory = {}
        for at in range(0, 3601, 60):
            observe(memory, observation(at))
        self.assertAlmostEqual(memory['actual_hours']['0']['kwh'], 2)
        self.assertEqual(memory['actual_hours']['0']['coverage_seconds'], 3600)

    def test_cross_hour_segment_splits_energy(self):
        memory = {}
        observe(memory, observation(3540))
        observe(memory, observation(3660))
        self.assertEqual(memory['actual_hours']['0']['coverage_seconds'], 60)
        self.assertEqual(memory['actual_hours']['3600']['coverage_seconds'], 60)
        self.assertAlmostEqual(sum(r['kwh'] for r in memory['actual_hours'].values()), 2 / 30)

    def test_outages_and_meter_resets_are_not_zero_production(self):
        memory = {}
        observe(memory, observation(0, energy=20))
        observe(memory, observation(60, valid=False, power=None))
        observe(memory, observation(120, energy=21))
        observe(memory, observation(180, energy=0))
        observe(memory, observation(600))
        row = memory['actual_hours']['0']
        self.assertEqual(row['coverage_seconds'], 0)
        self.assertEqual(row['invalid_seconds'], 180)

    def test_freezes_issued_prediction_and_rejects_partial_actuals(self):
        memory = {}
        row = candidate(start=3600, issued_at=0)
        issue(memory, [row])
        row['raw_kwh'] = 999
        self.assertEqual(memory['pending'][0]['raw_kwh'], 2)
        memory['actual_hours'] = {'3600': {'kwh': 1, 'coverage_seconds': 1800, 'invalid_seconds': 0}}
        finalize(memory, 7200)
        self.assertFalse(memory['scored'][0]['accepted'])
        self.assertEqual(memory['pending'], [])
        self.assertEqual(scorecard(memory)['excluded_forecasts'], 1)

    def test_scores_only_after_target_finishes(self):
        memory = {}
        issue(memory, [candidate(start=3600, issued_at=0)])
        memory['actual_hours'] = {'3600': {'kwh': 1.5, 'coverage_seconds': 3600, 'invalid_seconds': 0}}
        finalize(memory, 7199)
        self.assertFalse(memory['scored'])
        finalize(memory, 7200)
        metrics = scorecard(memory)['by_horizon']['1-3h']
        self.assertAlmostEqual(metrics['all']['raw']['mae_kwh'], .5)
        self.assertEqual(metrics['trained_only']['samples'], 0)

    def training_rows(self):
        return [dict(candidate(start=day*86400), actual_kwh=100, accepted=True,
                     trained=False, learned_kwh=2) for day in range(1, 10)]

    def test_regularized_learning_is_bounded(self):
        memory = {'scored': self.training_rows()}
        value, trained, count = prediction(memory, candidate(start=12*86400))
        self.assertTrue(trained)
        self.assertEqual(count, 9)
        self.assertEqual(value, 2.5)
        for row in memory['scored']:
            row['actual_kwh'] = 0
        self.assertGreaterEqual(prediction(memory, candidate(start=12*86400))[0], 1.5)

    def test_future_outcomes_never_leak_into_training(self):
        memory = {'scored': self.training_rows()}
        self.assertEqual(prediction(memory, candidate(start=3*86400)), (2, False, 2))

    def test_duplicate_targets_do_not_inflate_training_evidence(self):
        row = self.training_rows()[0]
        memory = {'scored': [deepcopy(row) for _ in range(100)]}
        self.assertEqual(prediction(memory, candidate()), (2, False, 1))

    def test_weather_and_horizon_groups_are_separate(self):
        memory = {'scored': self.training_rows()}
        self.assertFalse(prediction(memory, candidate(start=12*86400, sky_bin='cloudy'))[1])
        self.assertFalse(prediction(memory, candidate(start=12*86400, lead='12-25h'))[1])

    def test_history_is_pruned_and_restart_gap_is_not_integrated(self):
        memory = {'scored': self.training_rows(), 'actual_hours': {'0': {}}}
        finalize(memory, 200*86400)
        self.assertEqual(memory['scored'], [])
        self.assertEqual(memory['actual_hours'], {})
        observe(memory, observation(200*86400))
        self.assertEqual(memory['actual_hours'], {})
