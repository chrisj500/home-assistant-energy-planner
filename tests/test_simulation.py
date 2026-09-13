from __future__ import annotations

from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from simulation import (  # noqa: E402
    ControllerSettings,
    full_day_solar_curve,
    simulate_energy_flow,
)


class PhysicalSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capacities = (18.432, 12.288, 18.432)
        self.duration_h = 12.4
        self.curve = full_day_solar_curve(
            solar_forecast_kwh=45.0,
            daylight_hours=self.duration_h,
            peak_elapsed_h=6.2,
        )

    def test_controller_absorbs_smooth_surplus_with_plenty_of_headroom(self) -> None:
        result = simulate_energy_flow(
            duration_h=self.duration_h,
            solar_power_kw=self.curve,
            average_load_kw=2.6,
            bank_socs_pct=(22, 23, 22),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
            controller=ControllerSettings(source="test"),
        )

        self.assertLess(result.predicted_export_kwh, 1.0)
        self.assertGreater(result.solar_to_battery_ac_kwh, 1.0)
        self.assertLess(result.capacity_limited_export_kwh, 0.1)

    def test_disabled_controller_does_not_assume_battery_capture(self) -> None:
        result = simulate_energy_flow(
            duration_h=self.duration_h,
            solar_power_kw=self.curve,
            average_load_kw=2.6,
            bank_socs_pct=(22, 23, 22),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
            controller=ControllerSettings(enabled=False, source="disabled"),
        )

        self.assertEqual(result.stored_charge_kwh, 0.0)
        self.assertGreater(result.predicted_export_kwh, 1.0)

    def test_charge_efficiency_loss_is_not_export(self) -> None:
        high_eff = simulate_energy_flow(
            duration_h=self.duration_h,
            solar_power_kw=self.curve,
            average_load_kw=2.6,
            bank_socs_pct=(22, 23, 22),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=1.0,
            controller=ControllerSettings(source="test"),
        )
        low_eff = simulate_energy_flow(
            duration_h=self.duration_h,
            solar_power_kw=self.curve,
            average_load_kw=2.6,
            bank_socs_pct=(22, 23, 22),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.80,
            controller=ControllerSettings(source="test"),
        )

        self.assertLess(low_eff.stored_charge_kwh, high_eff.stored_charge_kwh)
        self.assertAlmostEqual(
            low_eff.predicted_export_kwh,
            high_eff.predicted_export_kwh,
            places=5,
        )

    def test_full_battery_export_is_classified_as_capacity_limited(self) -> None:
        result = simulate_energy_flow(
            duration_h=self.duration_h,
            solar_power_kw=self.curve,
            average_load_kw=1.0,
            bank_socs_pct=(100, 100, 100),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
            controller=ControllerSettings(source="test"),
        )

        self.assertGreater(result.predicted_export_kwh, 1.0)
        self.assertAlmostEqual(
            result.predicted_export_kwh,
            result.capacity_limited_export_kwh,
            places=5,
        )


if __name__ == "__main__":
    unittest.main()
