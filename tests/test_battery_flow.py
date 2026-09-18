from __future__ import annotations

from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from battery_flow import integrate_signed_power, normalize_power_w  # noqa: E402


class BatteryFlowTests(unittest.TestCase):
    def test_normalize_power_units(self) -> None:
        self.assertEqual(normalize_power_w(1000, "W"), 1000)
        self.assertEqual(normalize_power_w(1.5, "kW"), 1500)
        self.assertEqual(normalize_power_w(0.002, "MW"), 2000)
        self.assertEqual(normalize_power_w(750, None), 750)

    def test_constant_charge_integrates_to_kwh(self) -> None:
        result = integrate_signed_power(1000, 1000, 3600)
        self.assertAlmostEqual(result.charged_kwh, 1.0, places=6)
        self.assertEqual(result.discharged_kwh, 0.0)

    def test_constant_discharge_integrates_to_kwh(self) -> None:
        result = integrate_signed_power(-2000, -2000, 1800)
        self.assertEqual(result.charged_kwh, 0.0)
        self.assertAlmostEqual(result.discharged_kwh, 1.0, places=6)

    def test_zero_crossing_does_not_cancel_energy(self) -> None:
        result = integrate_signed_power(1000, -1000, 3600)
        self.assertAlmostEqual(result.charged_kwh, 0.25, places=6)
        self.assertAlmostEqual(result.discharged_kwh, 0.25, places=6)


if __name__ == "__main__":
    unittest.main()
