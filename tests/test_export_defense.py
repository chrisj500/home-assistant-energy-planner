from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from export_defense import assess_export_defense  # noqa: E402


@dataclass
class Plan:
    end_soc_pct: float
    headroom_shortfall_kwh: float = 0.0
    capacity_export_kwh: float = 0.0


class ExportDefenseTests(unittest.TestCase):
    def test_nominal_full_is_risk_even_without_simulated_export(self) -> None:
        result = assess_export_defense(
            nominal=Plan(100.0),
            defense=Plan(100.0),
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            forecast_underprediction_soc=12.0,
            charge_efficiency=0.9,
        )
        self.assertTrue(result.risk)
        self.assertGreater(result.headroom_kwh, 5.8)
        self.assertEqual(result.reason, "projected_saturation")

    def test_high_solar_defense_case_drives_headroom(self) -> None:
        result = assess_export_defense(
            nominal=Plan(88.0),
            defense=Plan(100.0, headroom_shortfall_kwh=4.0, capacity_export_kwh=4.4),
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            forecast_underprediction_soc=6.0,
            charge_efficiency=0.9,
        )
        self.assertTrue(result.risk)
        self.assertGreaterEqual(result.headroom_kwh, 4.0)
        self.assertEqual(result.reason, "export_defense_capacity_shortfall")

    def test_low_soc_clear_when_error_band_does_not_reach_ceiling(self) -> None:
        result = assess_export_defense(
            nominal=Plan(65.0),
            defense=Plan(72.0),
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            forecast_underprediction_soc=10.0,
            charge_efficiency=0.9,
        )
        self.assertFalse(result.risk)
        self.assertEqual(result.headroom_kwh, 0.0)

    def test_stress_case_and_historical_error_are_not_double_counted(self) -> None:
        result = assess_export_defense(
            nominal=Plan(54.4),
            defense=Plan(78.8),
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            forecast_underprediction_soc=23.2,
            charge_efficiency=0.9,
        )
        self.assertFalse(result.risk)
        self.assertEqual(result.headroom_kwh, 0.0)
        self.assertAlmostEqual(result.risk_adjusted_ceiling_pct, 76.8)

    def test_forecast_error_band_creates_headroom_before_saturation(self) -> None:
        result = assess_export_defense(
            nominal=Plan(92.0),
            defense=Plan(94.0),
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            forecast_underprediction_soc=10.0,
            charge_efficiency=0.9,
        )
        self.assertTrue(result.risk)
        self.assertAlmostEqual(result.risk_adjusted_ceiling_pct, 90.0)
        self.assertAlmostEqual(result.uncertainty_headroom_kwh, 0.98304, places=4)

    def test_no_margin_is_subtracted_from_direct_export_need(self) -> None:
        result = assess_export_defense(
            nominal=Plan(80.0),
            defense=Plan(100.0, headroom_shortfall_kwh=6.0, capacity_export_kwh=6.5),
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            forecast_underprediction_soc=0.0,
            charge_efficiency=0.9,
        )
        self.assertEqual(result.direct_headroom_kwh, 6.0)
        self.assertEqual(result.headroom_kwh, 6.0)


if __name__ == "__main__":
    unittest.main()
