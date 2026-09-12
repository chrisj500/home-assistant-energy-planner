from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from projection import project_sunset_soc  # noqa: E402


class SunsetProjectionTests(unittest.TestCase):
    def test_sep12_snapshot_projection(self) -> None:
        """Reproduce the 2026-09-12 16:35 Energy Planning snapshot."""
        load_kw = 0.45 * 2.594 + 0.55 * 2.751
        hours_to_sunset = 7.40 / load_kw
        now = datetime(2026, 9, 12, 20, 35, 54, tzinfo=timezone.utc)

        result = project_sunset_soc(
            now=now,
            sunset=now + timedelta(hours=hours_to_sunset),
            current_soc_pct=22.4,
            capacity_kwh=49.152,
            charge_limit_pct=100,
            remaining_solar_kwh=4.44,
            expected_load_remaining_kwh=7.40,
            current_solar_w=3224,
            peak_time=now - timedelta(hours=6),
            preferred_import_w=250,
            harvest_capture_factor=0.8853,
            charge_efficiency=0.90,
            step_minutes=1,
        )

        self.assertAlmostEqual(result.projected_soc, 22.83, places=2)
        self.assertAlmostEqual(result.projected_charge_kwh, 0.211, places=3)
        self.assertEqual(result.model, "post_peak_linear")

    def test_daytime_deficit_never_discharges_battery(self) -> None:
        now = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)
        result = project_sunset_soc(
            now=now,
            sunset=now + timedelta(hours=2),
            current_soc_pct=50,
            capacity_kwh=49.152,
            charge_limit_pct=100,
            remaining_solar_kwh=1.0,
            expected_load_remaining_kwh=8.0,
            current_solar_w=500,
            peak_time=now - timedelta(hours=3),
        )
        self.assertGreaterEqual(result.projected_soc, 50.0)

    def test_charge_limit_caps_projection(self) -> None:
        now = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)
        result = project_sunset_soc(
            now=now,
            sunset=now + timedelta(hours=3),
            current_soc_pct=79,
            capacity_kwh=49.152,
            charge_limit_pct=80,
            remaining_solar_kwh=20.0,
            expected_load_remaining_kwh=1.0,
            current_solar_w=7000,
            peak_time=now - timedelta(hours=1),
        )
        self.assertAlmostEqual(result.projected_soc, 80.0, places=6)


if __name__ == "__main__":
    unittest.main()
