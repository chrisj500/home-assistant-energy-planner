from __future__ import annotations

from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from ev_learning import (  # noqa: E402
    infer_wall_energy_full_kwh,
    learned_wall_energy_full_kwh,
    residual_after_ev_charge,
)


class EvLearningTests(unittest.TestCase):
    def test_infer_wall_energy_full_from_session(self) -> None:
        inferred = infer_wall_energy_full_kwh(
            wall_energy_kwh=4.0,
            start_soc_pct=40.0,
            end_soc_pct=60.0,
        )
        self.assertEqual(inferred, 20.0)

    def test_infer_wall_energy_rejects_small_soc_change(self) -> None:
        inferred = infer_wall_energy_full_kwh(
            wall_energy_kwh=1.0,
            start_soc_pct=40.0,
            end_soc_pct=42.0,
        )
        self.assertIsNone(inferred)

    def test_learned_full_energy_uses_median_then_fallback(self) -> None:
        learned, count = learned_wall_energy_full_kwh([19.0, 21.0, 20.0], 24.0)
        self.assertEqual(learned, 20.0)
        self.assertEqual(count, 3)
        fallback, fallback_count = learned_wall_energy_full_kwh([], 24.0)
        self.assertEqual(fallback, 24.0)
        self.assertEqual(fallback_count, 0)

    def test_residual_after_ev_charge_matches_physical_accounting(self) -> None:
        headroom, export = residual_after_ev_charge(
            headroom_shortfall_kwh=27.44,
            capacity_export_kwh=30.48,
            ev_solar_energy_kwh=8.55,
            charge_efficiency=0.90,
        )
        self.assertAlmostEqual(headroom, 19.745, places=3)
        self.assertAlmostEqual(export, 21.93, places=2)


if __name__ == "__main__":
    unittest.main()
