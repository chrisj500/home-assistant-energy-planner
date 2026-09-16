from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh  # noqa: E402
from headroom import correct_current_day_points  # noqa: E402


class HeadroomForecastCorrectionTests(unittest.TestCase):
    def test_scales_only_current_day_to_corrected_remaining_energy(self) -> None:
        now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
        sunset = now + timedelta(hours=6)
        tomorrow = now + timedelta(days=1)
        points = [
            IntervalPoint(now, 4000.0),
            IntervalPoint(now + timedelta(hours=3), 6000.0),
            IntervalPoint(sunset, 0.0),
            IntervalPoint(tomorrow, 3000.0),
            IntervalPoint(tomorrow + timedelta(hours=3), 5000.0),
        ]
        raw = integrate_interval_energy_kwh(points, now, sunset)
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunset=sunset,
            corrected_remaining_kwh=raw * 1.4,
        )

        corrected = integrate_interval_energy_kwh(correction.points, now, sunset)
        self.assertAlmostEqual(correction.scale_factor, 1.4, places=3)
        self.assertAlmostEqual(corrected, raw * 1.4, places=2)
        self.assertEqual(correction.points[-1].watts, points[-1].watts)
        self.assertEqual(
            correction.source,
            "locally_corrected_current_day_interval_curve",
        )

    def test_falls_back_to_raw_curve_without_corrected_total(self) -> None:
        now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
        sunset = now + timedelta(hours=4)
        points = [
            IntervalPoint(now, 5000.0),
            IntervalPoint(sunset, 0.0),
        ]
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunset=sunset,
            corrected_remaining_kwh=None,
        )
        self.assertEqual(correction.scale_factor, 1.0)
        self.assertEqual(correction.points, points)
        self.assertEqual(correction.source, "raw_interval_curve")

    def test_scale_is_bounded_against_bad_upstream_values(self) -> None:
        now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
        sunset = now + timedelta(hours=4)
        points = [
            IntervalPoint(now, 1000.0),
            IntervalPoint(sunset, 1000.0),
        ]
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunset=sunset,
            corrected_remaining_kwh=1000.0,
        )
        self.assertEqual(correction.scale_factor, 4.0)


if __name__ == "__main__":
    unittest.main()
