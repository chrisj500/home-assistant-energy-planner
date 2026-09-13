from __future__ import annotations

from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from simulation import ControllerSettings, simulate_energy_flow  # noqa: E402


class PhysicalSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capacities = (18.432, 12.288, 18.432)
        self.controller = ControllerSettings(source="test")

    def test_solar_surplus_with_headroom_is_captured_not_exported(self) -> None:
        result = simulate_energy_flow(
            duration_h=2,
            solar_power_kw=lambda _t: 6.0,
            average_load_kw=2.0,
            bank_socs_pct=(20, 20, 20),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
            controller=self.controller,
        )
        self.assertAlmostEqual(result.solar_to_battery_ac_kwh, 8.0, places=5)
        self.assertAlmostEqual(result.stored_charge_kwh, 7.2, places=5)
        self.assertAlmostEqual(result.predicted_export_kwh, 0.0, places=8)
        self.assertAlmostEqual(result.predicted_grid_import_kwh, 0.0, places=8)
        self.assertEqual(result.grid_to_battery_ac_kwh, 0.0)

    def test_charge_efficiency_loss_is_not_export(self) -> None:
        result = simulate_energy_flow(
            duration_h=1,
            solar_power_kw=lambda _t: 5.0,
            average_load_kw=2.0,
            bank_socs_pct=(20, 20, 20),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.80,
            controller=self.controller,
        )
        self.assertAlmostEqual(result.solar_to_battery_ac_kwh, 3.0, places=5)
        self.assertAlmostEqual(result.stored_charge_kwh, 2.4, places=5)
        self.assertAlmostEqual(result.predicted_export_kwh, 0.0, places=8)

    def test_house_deficit_is_grid_import_without_battery_discharge(self) -> None:
        result = simulate_energy_flow(
            duration_h=2,
            solar_power_kw=lambda _t: 1.0,
            average_load_kw=3.0,
            bank_socs_pct=(70, 70, 70),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
            controller=self.controller,
        )
        self.assertAlmostEqual(result.predicted_grid_import_kwh, 4.0, places=5)
        self.assertAlmostEqual(result.stored_charge_kwh, 0.0, places=8)
        self.assertAlmostEqual(result.aggregate_soc_pct, 70.0, places=8)

    def test_full_battery_export_is_capacity_limited(self) -> None:
        result = simulate_energy_flow(
            duration_h=1,
            solar_power_kw=lambda _t: 6.0,
            average_load_kw=2.0,
            bank_socs_pct=(100, 100, 100),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
            controller=self.controller,
        )
        self.assertAlmostEqual(result.predicted_export_kwh, 4.0, places=5)
        self.assertAlmostEqual(result.capacity_limited_export_kwh, 4.0, places=5)
        self.assertAlmostEqual(result.power_limited_export_kwh, 0.0, places=8)

    def test_surplus_above_installed_charge_power_is_power_limited(self) -> None:
        result = simulate_energy_flow(
            duration_h=1,
            solar_power_kw=lambda _t: 16.0,
            average_load_kw=2.0,
            bank_socs_pct=(10, 10, 10),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
            controller=self.controller,
        )
        self.assertAlmostEqual(result.power_limited_export_kwh, 2.3, places=5)
        self.assertAlmostEqual(result.capacity_limited_export_kwh, 0.0, places=8)
        self.assertAlmostEqual(result.predicted_export_kwh, 2.3, places=5)

    def test_preferred_import_is_not_an_energy_source(self) -> None:
        common = dict(
            duration_h=2,
            solar_power_kw=lambda _t: 6.0,
            average_load_kw=2.0,
            bank_socs_pct=(20, 20, 20),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
        )
        low = simulate_energy_flow(
            **common,
            controller=ControllerSettings(preferred_import_w=0, source="test"),
        )
        high = simulate_energy_flow(
            **common,
            controller=ControllerSettings(preferred_import_w=1500, source="test"),
        )
        self.assertEqual(low.grid_to_battery_ac_kwh, 0.0)
        self.assertEqual(high.grid_to_battery_ac_kwh, 0.0)
        self.assertAlmostEqual(low.stored_charge_kwh, high.stored_charge_kwh, places=8)
        self.assertAlmostEqual(low.predicted_grid_import_kwh, high.predicted_grid_import_kwh, places=8)

    def test_disabled_controller_exports_surplus_but_does_not_buy_energy(self) -> None:
        result = simulate_energy_flow(
            duration_h=1,
            solar_power_kw=lambda _t: 6.0,
            average_load_kw=2.0,
            bank_socs_pct=(20, 20, 20),
            bank_capacities_kwh=self.capacities,
            charge_limit_pct=100,
            charge_efficiency=0.90,
            controller=ControllerSettings(enabled=False, source="test"),
        )
        self.assertAlmostEqual(result.control_limited_export_kwh, 4.0, places=5)
        self.assertAlmostEqual(result.predicted_export_kwh, 4.0, places=5)
        self.assertEqual(result.grid_to_battery_ac_kwh, 0.0)


if __name__ == "__main__":
    unittest.main()
