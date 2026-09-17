from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh, power_at  # noqa: E402
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

    def test_live_anchor_matches_actual_power_and_preserves_remaining_energy(self) -> None:
        now = datetime(2026, 9, 17, 19, 30, tzinfo=timezone.utc)
        sunrise = now - timedelta(hours=8)
        sunset = now + timedelta(hours=3)
        points = [
            IntervalPoint(now - timedelta(minutes=15), 2600.0),
            IntervalPoint(now + timedelta(minutes=15), 2400.0),
            IntervalPoint(now + timedelta(hours=1), 1800.0),
            IntervalPoint(now + timedelta(hours=2), 900.0),
            IntervalPoint(sunset, 0.0),
            IntervalPoint(now + timedelta(days=1), 4500.0),
        ]
        corrected_total = 5.5
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunrise=sunrise,
            sunset=sunset,
            corrected_remaining_kwh=corrected_total,
            actual_solar_w=4200.0,
        )

        self.assertEqual(
            correction.source,
            "live_anchored_current_day_interval_curve",
        )
        self.assertAlmostEqual(power_at(correction.points, now), 4200.0, places=2)
        self.assertAlmostEqual(
            integrate_interval_energy_kwh(correction.points, now, sunset),
            corrected_total,
            places=2,
        )
        self.assertAlmostEqual(correction.live_anchor_w or 0.0, 4200.0, places=2)
        self.assertGreater(correction.live_blend_minutes or 0.0, 0.0)
        self.assertEqual(correction.points[-1].watts, points[-1].watts)

    def test_live_anchor_can_reduce_overstated_current_power_without_changing_energy(self) -> None:
        now = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)
        sunrise = now - timedelta(hours=7)
        sunset = now + timedelta(hours=4)
        points = [
            IntervalPoint(now, 6500.0),
            IntervalPoint(now + timedelta(hours=1), 5000.0),
            IntervalPoint(now + timedelta(hours=2), 3000.0),
            IntervalPoint(sunset, 0.0),
        ]
        raw = integrate_interval_energy_kwh(points, now, sunset)
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunrise=sunrise,
            sunset=sunset,
            corrected_remaining_kwh=raw,
            actual_solar_w=3500.0,
        )

        self.assertAlmostEqual(power_at(correction.points, now), 3500.0, places=2)
        self.assertAlmostEqual(
            integrate_interval_energy_kwh(correction.points, now, sunset),
            raw,
            places=2,
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

    def test_pre_sunrise_zero_uses_raw_curve_during_rollover(self) -> None:
        now = datetime(2026, 9, 17, 4, 5, tzinfo=timezone.utc)
        sunrise = now + timedelta(hours=6)
        sunset = now + timedelta(hours=18)
        points = [
            IntervalPoint(now, 0.0),
            IntervalPoint(sunrise, 500.0),
            IntervalPoint(sunrise + timedelta(hours=4), 5000.0),
            IntervalPoint(sunset, 0.0),
        ]
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunrise=sunrise,
            sunset=sunset,
            corrected_remaining_kwh=0.0,
        )
        self.assertEqual(correction.scale_factor, 1.0)
        self.assertEqual(correction.points, points)
        self.assertGreater(correction.raw_remaining_kwh, 0.05)
        self.assertEqual(
            correction.source,
            "raw_interval_curve_pre_sunrise_rollover",
        )

    def test_daylight_zero_remains_a_valid_local_correction(self) -> None:
        now = datetime(2026, 9, 17, 16, 0, tzinfo=timezone.utc)
        sunrise = now - timedelta(hours=6)
        sunset = now + timedelta(hours=4)
        points = [
            IntervalPoint(now, 4000.0),
            IntervalPoint(sunset, 0.0),
        ]
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunrise=sunrise,
            sunset=sunset,
            corrected_remaining_kwh=0.0,
        )
        self.assertEqual(correction.scale_factor, 0.25)
        self.assertEqual(
            correction.source,
            "locally_corrected_current_day_interval_curve",
        )

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
