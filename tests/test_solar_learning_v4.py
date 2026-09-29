"""Tests for the continuous weather-aware v4 solar shadow learner."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))

from solar_learning_v4 import (  # noqa: E402
    MODEL_NAME,
    annotate_latest,
    ensure_shadow,
    prediction,
    scorecard,
)


BASE = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


def forecast_row(
    day_offset,
    hour,
    *,
    sky,
    raw=2.0,
    actual=1.5,
    lead_hours=18.0,
    accepted=True,
):
    start_dt = BASE + timedelta(days=day_offset, hours=hour)
    issued_dt = start_dt - timedelta(hours=lead_hours)
    return {
        "issued_at": issued_dt.timestamp(),
        "provider_received_at": issued_dt.timestamp() - 60,
        "start": start_dt.timestamp(),
        "end": (start_dt + timedelta(hours=1)).timestamp(),
        "day": start_dt.date().isoformat(),
        "hour": hour,
        "lead": "12-24h" if lead_hours >= 12 else "3-12h" if lead_hours >= 3 else "0-3h",
        "raw_kwh": raw,
        "live_kwh": raw,
        "learned_kwh": raw,
        "sky_bin": "clear" if sky < 0.3 else "mixed" if sky < 0.8 else "cloudy",
        "forecast_sky": sky,
        "forecast_temperature_c": 20.0,
        "observed_at_issue": {
            "radiation_wm2": 200.0,
            "power_w": 1500.0,
            "valid": True,
        },
        "actual_kwh": actual,
        "coverage": 1.0,
        "accepted": accepted,
        "trained": False,
    }


class SolarLearningV4Tests(unittest.TestCase):
    def mixed_cloud_history(self):
        rows = []
        skies = [0.45, 0.55, 0.60, 0.68, 0.76, 0.84]
        index = 0
        for day in range(3):
            for hour in (10, 11):
                rows.append(
                    forecast_row(
                        day,
                        hour,
                        sky=skies[index],
                        actual=1.4,
                    )
                )
                index += 1
        return rows

    def test_continuous_cloud_model_trains_across_mixed_and_cloudy_boundary(self):
        memory = {"scored": self.mixed_cloud_history()}
        candidate = forecast_row(
            4,
            11,
            sky=0.82,
            actual=0.0,
        )
        value, trained, metadata = prediction(memory, candidate)

        self.assertTrue(trained)
        self.assertGreaterEqual(metadata["training_samples"], 6)
        self.assertGreaterEqual(metadata["training_days"], 3)
        self.assertGreaterEqual(metadata["effective_samples"], 3.0)
        # The learned residual should move below the 2 kWh provider baseline.
        self.assertLess(value, candidate["raw_kwh"])
        self.assertEqual(candidate["sky_bin"], "cloudy")
        self.assertTrue(any(row["sky_bin"] == "mixed" for row in memory["scored"]))

    def test_future_outcomes_never_leak_into_v4_training(self):
        candidate = forecast_row(2, 10, sky=0.7)
        # These rows are similar, but all targets finish after the candidate was
        # issued and therefore must not be usable training truth.
        future = [
            forecast_row(3 + day, 10 + (day % 2), sky=0.7)
            for day in range(6)
        ]
        value, trained, metadata = prediction({"scored": future}, candidate)

        self.assertFalse(trained)
        self.assertEqual(value, candidate["raw_kwh"])
        self.assertEqual(metadata["training_samples"], 0)

    def test_duplicate_target_hours_do_not_inflate_neighbor_evidence(self):
        base = self.mixed_cloud_history()[0]
        duplicates = []
        for offset in range(20):
            row = dict(base)
            row["issued_at"] -= offset * 300
            duplicates.append(row)
        candidate = forecast_row(4, 10, sky=0.5)
        _, trained, metadata = prediction({"scored": duplicates}, candidate)

        self.assertFalse(trained)
        self.assertEqual(metadata["training_samples"], 1)

    def test_existing_history_is_backfilled_with_frozen_v4_predictions(self):
        memory = {"scored": self.mixed_cloud_history()}
        audit = ensure_shadow(memory)

        self.assertEqual(audit["rows_backfilled"], len(memory["scored"]))
        self.assertTrue(all(row["v4_model_version"] == 4 for row in memory["scored"]))
        self.assertTrue(all("v4_learned_kwh" in row for row in memory["scored"]))
        # Early rows cannot see later outcomes; the backfill remains an honest
        # as-issued replay rather than an in-sample refit.
        self.assertFalse(memory["scored"][0]["v4_trained"])

    def test_new_pending_forecast_is_frozen_and_scored_separately_from_v3(self):
        memory = {"scored": self.mixed_cloud_history()}
        candidate = forecast_row(4, 11, sky=0.82)
        candidate.pop("actual_kwh")
        candidate.pop("coverage")
        candidate.pop("accepted")
        memory["pending"] = [candidate]

        changed = annotate_latest(memory, candidate["issued_at"])
        self.assertEqual(changed, 1)
        self.assertTrue(candidate["v4_trained"])
        self.assertEqual(candidate["v4_model_version"], 4)
        self.assertEqual(MODEL_NAME, "continuous_weather_residual_v4")

    def test_v4_scorecard_compares_same_targets_against_raw_v3_and_v4(self):
        rows = self.mixed_cloud_history()
        memory = {"scored": rows}
        ensure_shadow(memory)
        metrics = scorecard(memory)

        self.assertEqual(metrics["model_version"], 4)
        self.assertEqual(metrics["model"], MODEL_NAME)
        group = metrics["by_horizon"]["12-24h"]["all"]
        self.assertIn("raw", group)
        self.assertIn("v3", group)
        self.assertIn("v4", group)
        self.assertEqual(group["samples"], 6)


if __name__ == "__main__":
    unittest.main()
