from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from projection import project_sunset_soc  # noqa: E402
from simulation import ControllerSettings  # noqa: E402


class SunsetProjectionTests(unittest.TestCase):
    def test_sep12_snapshot_projection(self) -> None:
        """Keep the historical snapshot in the same neighborhood without fake export."""
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
            harvest_capture_factor=0.50,
            charge_efficiency=0.90,
            bank_socs_pct=(22, 23, 22),
            bank_capacities_kwh=(18.432, 12.288, 18.432),
            controller_settings=ControllerSettings(source="test"),
            step_minutes=1,
        )

        self.assertGreater(result.projected_soc, 22.4)
        self.assertLess(result.projected_soc, 23.2)
        self.assertLess(result.predicted_export_kwh, 0.2)
        self.assertIn("controller_mirror", result.model)

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
            bank_socs_pct=(50, 50, 50),
            bank_capacities_kwh=(18.432, 12.288, 18.432),
        )
        self.assertGreaterEqual(result.projected_soc, 50.0)

    def test_charge_limit_caps_projection_and_exposes_capacity_export(self) -> None:
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
            bank_socs_pct=(79, 79, 79),
            bank_capacities_kwh=(18.432, 12.288, 18.432),
        )
        self.assertLessEqual(result.projected_soc, 80.0 + 1e-6)
        self.assertGreater(result.capacity_limited_export_kwh, 0.0)

    def test_harvest_factor_does_not_change_live_physical_forecast(self) -> None:
        now = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)
        kwargs = dict(
            now=now,
            sunset=now + timedelta(hours=4),
            current_soc_pct=30,
            capacity_kwh=49.152,
            charge_limit_pct=100,
            remaining_solar_kwh=12.0,
            expected_load_remaining_kwh=6.0,
            current_solar_w=5000,
            peak_time=now - timedelta(hours=1),
            bank_socs_pct=(30, 30, 30),
            bank_capacities_kwh=(18.432, 12.288, 18.432),
        )
        low = project_sunset_soc(**kwargs, harvest_capture_factor=0.5)
        high = project_sunset_soc(**kwargs, harvest_capture_factor=1.0)
        self.assertAlmostEqual(low.projected_soc, high.projected_soc, places=8)
        self.assertAlmostEqual(low.predicted_export_kwh, high.predicted_export_kwh, places=8)


if __name__ == "__main__":
    unittest.main()
