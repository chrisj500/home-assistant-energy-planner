import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from solar_profile import prediction, scorecard


class IndependentSolarProfileTests(unittest.TestCase):
    def setUp(self):
        self.issue = datetime(2026, 8, 20, 0, tzinfo=timezone.utc).timestamp()
        self.memory = {"scored": []}
        for day_index in range(5):
            day = datetime(2026, 8, 1 + day_index, tzinfo=timezone.utc)
            for hour in (10, 11, 12):
                start = day.replace(hour=hour).timestamp()
                self.memory["scored"].append({
                    "issued_at": start - 50 * 3600,
                    "start": start,
                    "end": start + 3600,
                    "day": day.date().isoformat(),
                    "hour": hour,
                    "lead": "48-72h",
                    "lead_hours": 50,
                    "accepted": True,
                    "actual_kwh": 4.5,
                    "raw_kwh": 99.0,
                    "forecast_sky": 0.2,
                    "forecast_temperature_c": 20,
                    "target_sun_elevation_deg": 40,
                    "target_sun_azimuth_deg": 180,
                    "day_of_year": 213,
                })
        self.candidate = {
            "issued_at": self.issue,
            "start": self.issue + 50 * 3600,
            "end": self.issue + 51 * 3600,
            "hour": 12,
            "lead": "48-72h",
            "lead_hours": 50,
            "forecast_sky": 0.2,
            "forecast_temperature_c": 20,
            "target_sun_elevation_deg": 40,
            "target_sun_azimuth_deg": 180,
            "day_of_year": 213,
            "raw_kwh": 1000,
        }

    def test_direct_output_uses_measured_ac_targets_not_raw_energy(self):
        first = prediction(self.memory, self.candidate)
        changed = dict(self.candidate, raw_kwh=0.0)
        second = prediction(self.memory, changed)
        self.assertTrue(first[1])
        self.assertAlmostEqual(first[0], 4.5)
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2]["days"], 5)

    def test_requires_five_distinct_days_and_prior_completed_targets(self):
        memory = {"scored": self.memory["scored"][:9]}
        self.assertFalse(prediction(memory, self.candidate)[1])
        future_memory = {"scored": [dict(row, end=self.issue + 1) for row in self.memory["scored"]]}
        self.assertFalse(prediction(future_memory, self.candidate)[1])

    def test_validation_rejects_better_mae_that_increases_underforecast_bias(self):
        rows = []
        for i in range(12):
            day = datetime(2026, 8, 1 + i % 5, tzinfo=timezone.utc)
            start = (day.replace(hour=12) + timedelta(days=i // 5)).timestamp()
            rows.append({"accepted": True, "profile_model_version": 1, "profile_trained": True,
                         "lead": "48-72h", "start": start, "issued_at": start - 50 * 3600,
                         "day": day.date().isoformat(), "actual_kwh": 1.0,
                         "raw_kwh": 3.0, "profile_kwh": 0.5, "v4_learned_kwh": 3.0})
        group = scorecard({"scored": rows})["by_horizon"]["48-72h"]
        self.assertLess(group["profile"]["mae_kwh"], group["raw"]["mae_kwh"])
        self.assertFalse(group["beats_raw"])


if __name__ == "__main__":
    unittest.main()
