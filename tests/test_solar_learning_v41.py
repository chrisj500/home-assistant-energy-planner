"""Tests for the tuned continuous v4.1 solar shadow learner."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"),
)

from solar_learning_v41 import (  # noqa: E402
    MODEL_NAME,
    MODEL_VERSION,
    _horizon_scale,
    ensure_shadow,
    ensure_target_geometry,
    prediction,
    scorecard,
    solar_position,
)


BASE = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
LATITUDE = 38.9
LONGITUDE = -77.0


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
    row = {
        "issued_at": issued_dt.timestamp(),
        "provider_received_at": issued_dt.timestamp() - 60,
        "start": start_dt.timestamp(),
        "end": (start_dt + timedelta(hours=1)).timestamp(),
        "day": start_dt.date().isoformat(),
        "hour": hour,
        "lead": (
            "12-24h"
            if lead_hours >= 12
            else "3-12h"
            if lead_hours >= 3
            else "0-3h"
        ),
        "raw_kwh": raw,
        "live_kwh": raw,
        "learned_kwh": raw,
        "v4_learned_kwh": raw,
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
        "v4_trained": False,
    }
    ensure_target_geometry([row], LATITUDE, LONGITUDE)
    return row


def local_history(*, lead_hours=18.0, actual=1.4):
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
                    actual=actual,
                    lead_hours=lead_hours,
                )
            )
            index += 1
    return rows


class SolarLearningV41Tests(unittest.TestCase):
    def test_target_solar_geometry_changes_with_time_of_day(self):
        morning = datetime(2026, 9, 20, 12, tzinfo=timezone.utc).timestamp()
        midday = datetime(2026, 9, 20, 17, tzinfo=timezone.utc).timestamp()
        morning_elevation, morning_azimuth = solar_position(
            morning,
            LATITUDE,
            LONGITUDE,
        )
        midday_elevation, midday_azimuth = solar_position(
            midday,
            LATITUDE,
            LONGITUDE,
        )

        self.assertIsNotNone(morning_elevation)
        self.assertIsNotNone(midday_elevation)
        self.assertGreater(midday_elevation, morning_elevation)
        self.assertNotEqual(round(morning_azimuth, 1), round(midday_azimuth, 1))

    def test_mixed_cloud_history_can_train_cloudy_candidate(self):
        memory = {"scored": local_history()}
        candidate = forecast_row(4, 11, sky=0.82, actual=0.0)

        value, trained, metadata = prediction(memory, candidate)

        self.assertTrue(trained)
        self.assertGreaterEqual(metadata["training_samples"], 6)
        self.assertGreaterEqual(metadata["training_days"], 3)
        self.assertGreaterEqual(metadata["effective_samples"], 3.0)
        self.assertLess(value, candidate["raw_kwh"])

    def test_tight_lead_window_rejects_distant_lead_examples(self):
        candidate = forecast_row(4, 11, sky=0.65, lead_hours=18.0)
        distant = local_history(lead_hours=8.0)

        value, trained, metadata = prediction({"scored": distant}, candidate)

        self.assertFalse(trained)
        self.assertEqual(value, candidate["raw_kwh"])
        self.assertEqual(metadata["training_samples"], 0)
        self.assertEqual(metadata["lead_window_hours"], 4.0)

    def test_target_geometry_downweights_physically_different_hours(self):
        near = local_history(actual=1.4)
        far = local_history(actual=3.0)
        for row in far:
            row["target_sun_elevation_deg"] += 45.0
            row["target_sun_azimuth_deg"] = (
                row["target_sun_azimuth_deg"] + 140.0
            ) % 360.0
        candidate = forecast_row(4, 11, sky=0.65)

        value, trained, _ = prediction({"scored": near + far}, candidate)

        self.assertTrue(trained)
        # Physically similar rows say Forecast.Solar is high, while deliberately
        # dissimilar geometry says it is low. Geometry should preserve the local
        # downward signal.
        self.assertLess(value, candidate["raw_kwh"])

    def test_bad_prior_v41_performance_shrinks_correction_cap(self):
        rows = []
        for day in range(3):
            for hour in (9, 10, 11):
                row = forecast_row(
                    day,
                    hour,
                    sky=0.55,
                    raw=2.0,
                    actual=3.0,
                    lead_hours=18.0,
                )
                row["v41_trained"] = True
                row["v41_learned_kwh"] = 4.5
                row["v41_model_version"] = MODEL_VERSION
                rows.append(row)
        candidate = forecast_row(4, 10, sky=0.55, raw=2.0, lead_hours=18.0)

        value, trained, metadata = prediction({"scored": rows}, candidate)

        self.assertTrue(trained)
        self.assertGreaterEqual(metadata["performance_samples"], 8)
        self.assertLess(metadata["performance_scale"], 0.5)
        self.assertLess(metadata["correction_cap_fraction"], 0.08)
        self.assertLess((value / candidate["raw_kwh"]) - 1.0, 0.08)

    def test_longer_horizons_gain_correction_authority_more_slowly(self):
        near = forecast_row(4, 10, sky=0.5, lead_hours=2.0)
        medium = forecast_row(4, 10, sky=0.5, lead_hours=8.0)
        long = forecast_row(4, 10, sky=0.5, lead_hours=18.0)

        self.assertEqual(_horizon_scale(near), 1.0)
        self.assertEqual(_horizon_scale(medium), 0.65)
        self.assertEqual(_horizon_scale(long), 0.40)

    def test_early_evidence_never_gets_original_v4_forty_percent_cap(self):
        rows = local_history(actual=3.5)
        candidate = forecast_row(4, 11, sky=0.65, raw=2.0)

        value, trained, metadata = prediction({"scored": rows}, candidate)

        self.assertTrue(trained)
        self.assertLessEqual(metadata["performance_scale"], 0.35)
        self.assertLess(metadata["correction_cap_fraction"], 0.10)
        self.assertLess((value / candidate["raw_kwh"]) - 1.0, 0.10)

    def test_historical_backfill_remains_out_of_sample(self):
        memory = {"scored": local_history()}
        audit = ensure_shadow(memory, LATITUDE, LONGITUDE)

        self.assertEqual(audit["rows_backfilled"], len(memory["scored"]))
        self.assertTrue(
            all(row["v41_model_version"] == MODEL_VERSION for row in memory["scored"])
        )
        self.assertTrue(all("v41_learned_kwh" in row for row in memory["scored"]))
        self.assertFalse(memory["scored"][0]["v41_trained"])

    def test_scorecard_keeps_original_v4_and_adds_v41(self):
        memory = {"scored": local_history()}
        ensure_shadow(memory, LATITUDE, LONGITUDE)
        metrics = scorecard(memory)

        self.assertEqual(metrics["model"], MODEL_NAME)
        self.assertEqual(metrics["model_version"], MODEL_VERSION)
        group = metrics["by_horizon"]["12-24h"]["all"]
        self.assertIn("raw", group)
        self.assertIn("v3", group)
        self.assertIn("v4", group)
        self.assertIn("v4_1", group)
        self.assertEqual(group["samples"], 6)


if __name__ == "__main__":
    unittest.main()
