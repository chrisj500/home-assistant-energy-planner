from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from forecast_solar_shadow import (  # noqa: E402
    IntervalPoint,
    best_time_window,
    energy_remaining_from_result,
    forecast_horizon_days,
    integrate_interval_energy_kwh,
    interval_points_from_payload,
    interval_resolution_minutes,
)


class ForecastSolarShadowTests(unittest.TestCase):
    def test_interval_payload_parses_and_reports_15_min_resolution(self) -> None:
        now = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
        payload = {
            "result": {
                "watts": {
                    "2026-09-13 16:00:00": 1000,
                    "2026-09-13 16:15:00": 2000,
                    "2026-09-13 16:30:00": 3000,
                }
            }
        }
        points = interval_points_from_payload(payload, now, assume_utc=True)
        self.assertEqual(len(points), 3)
        self.assertEqual(interval_resolution_minutes(points), 15.0)

    def test_interval_energy_integrates_linear_curve(self) -> None:
        start = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
        points = [
            IntervalPoint(start, 0.0),
            IntervalPoint(start + timedelta(hours=1), 4000.0),
            IntervalPoint(start + timedelta(hours=2), 0.0),
        ]
        energy = integrate_interval_energy_kwh(
            points,
            start,
            start + timedelta(hours=2),
            step_minutes=1,
        )
        self.assertAlmostEqual(energy, 4.0, places=3)

    def test_horizon_counts_today_plus_six_days(self) -> None:
        start = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
        points = [
            IntervalPoint(start, 1000.0),
            IntervalPoint(start + timedelta(days=6, hours=1), 1000.0),
        ]
        self.assertEqual(forecast_horizon_days(points, start), 7)

    def test_clear_sky_remaining_sums_future_periods_only(self) -> None:
        now = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
        payload = {
            "result": {
                "watt_hours_period": {
                    "2026-09-13 15:45:00": 500,
                    "2026-09-13 16:15:00": 1000,
                    "2026-09-13 16:30:00": 1500,
                    "2026-09-13 22:00:00": 9999,
                }
            }
        }
        result = energy_remaining_from_result(
            payload,
            now,
            now + timedelta(hours=2),
        )
        self.assertEqual(result, 2.5)

    def test_best_time_window_selects_highest_energy(self) -> None:
        now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
        payload = {
            "result": [
                {
                    "start": "2026-09-13 12:00:00",
                    "end": "2026-09-13 13:00:00",
                    "watts": 3000,
                    "watthours": 2500,
                },
                {
                    "start": "2026-09-13 13:00:00",
                    "end": "2026-09-13 15:00:00",
                    "watts": 4500,
                    "watthours": 7000,
                },
            ]
        }
        best = best_time_window(payload, now)
        self.assertIsNotNone(best)
        assert best is not None
        self.assertEqual(best["watthours"], 7000.0)
        self.assertEqual(best["watts"], 4500.0)


if __name__ == "__main__":
    unittest.main()
