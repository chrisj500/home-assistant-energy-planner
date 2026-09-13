from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from simulation import ControllerSettings  # noqa: E402
from strategy import (  # noqa: E402
    STRATEGY_CREATE_HEADROOM,
    STRATEGY_HOLD,
    STRATEGY_PRESERVE_FOR_RESILIENCE,
    STRATEGY_USE_DISCRETIONARY_LOADS,
    plan_solar_period,
)


class SolarPeriodStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sunrise = datetime(2026, 9, 14, 10, 50, tzinfo=timezone.utc)
        self.sunset = datetime(2026, 9, 14, 23, 15, tzinfo=timezone.utc)
        self.peak = datetime(2026, 9, 14, 17, 0, tzinfo=timezone.utc)
        self.capacities = (18.432, 12.288, 18.432)
        self.common = {
            "sunrise": self.sunrise,
            "sunset": self.sunset,
            "capacity_kwh": 49.152,
            "charge_limit_pct": 100,
            "minimum_reserve_pct": 10,
            "peak_time": self.peak,
            "charge_efficiency": 0.90,
            "bank_capacities_kwh": self.capacities,
            "controller_settings": ControllerSettings(source="test"),
        }

    def test_plenty_of_headroom_does_not_invent_twelve_percent_export(self) -> None:
        """Regression for the v0.1.7 0.88-capture-factor export bug."""
        result = plan_solar_period(
            **self.common,
            current_soc_pct=22.28,
            bank_socs_pct=(22.0, 23.0, 22.0),
            solar_forecast_kwh=45.0,
            base_load_power_w=2600,
            harvest_capture_factor=0.88,
            ev_soc_pct=80,
            ev_target_soc_pct=100,
            ev_home=True,
        )

        self.assertEqual(result.strategy, STRATEGY_HOLD)
        self.assertEqual(result.recommended_overnight_discharge_kwh, 0.0)
        self.assertGreater(result.headroom_margin_kwh, 5.0)
        self.assertLess(result.predicted_export_kwh, 1.0)
        self.assertEqual(result.discretionary_energy_kwh, 0.0)

    def test_harvest_factor_is_not_physical_export_or_soc_loss(self) -> None:
        kwargs = dict(
            **self.common,
            current_soc_pct=30,
            bank_socs_pct=(30, 30, 30),
            solar_forecast_kwh=40,
            base_load_power_w=2400,
        )
        low = plan_solar_period(**kwargs, harvest_capture_factor=0.50)
        high = plan_solar_period(**kwargs, harvest_capture_factor=1.00)

        self.assertAlmostEqual(low.predicted_export_kwh, high.predicted_export_kwh, places=8)
        self.assertAlmostEqual(low.projected_sunset_soc_pct, high.projected_sunset_soc_pct, places=8)
        self.assertAlmostEqual(low.required_headroom_kwh, high.required_headroom_kwh, places=8)

    def test_near_full_battery_recommends_only_actual_missing_headroom(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=90,
            bank_socs_pct=(90, 90, 90),
            solar_forecast_kwh=50,
            base_load_power_w=1500,
            ev_soc_pct=40,
            ev_target_soc_pct=100,
            ev_home=True,
            ev_discretionary_allowed=False,
        )

        self.assertEqual(result.strategy, STRATEGY_CREATE_HEADROOM)
        self.assertGreater(result.capacity_limited_export_kwh, 1.0)
        self.assertGreater(result.headroom_shortfall_kwh, 1.0)
        self.assertAlmostEqual(
            result.recommended_overnight_discharge_kwh,
            result.headroom_shortfall_kwh,
            places=5,
        )
        self.assertGreaterEqual(result.planned_start_soc_pct, 10.0)

    def test_controlled_ev_can_be_preferred_only_when_explicitly_enabled(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=90,
            bank_socs_pct=(90, 90, 90),
            solar_forecast_kwh=50,
            base_load_power_w=1500,
            ev_soc_pct=40,
            ev_target_soc_pct=100,
            ev_home=True,
            ev_discretionary_allowed=True,
        )

        self.assertEqual(result.strategy, STRATEGY_USE_DISCRETIONARY_LOADS)
        self.assertEqual(result.recommended_overnight_discharge_kwh, 0.0)

    def test_effective_reserve_limits_headroom_creation(self) -> None:
        result = plan_solar_period(
            **{**self.common, "minimum_reserve_pct": 40},
            current_soc_pct=50,
            bank_socs_pct=(50, 50, 50),
            solar_forecast_kwh=100,
            base_load_power_w=1000,
            ev_discretionary_allowed=False,
        )

        max_discharge = 49.152 * 0.10
        self.assertLessEqual(result.recommended_overnight_discharge_kwh, max_discharge + 1e-6)
        self.assertGreaterEqual(result.planned_start_soc_pct, 40.0 - 1e-6)

    def test_daylight_mode_never_recommends_presolar_discharge(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=90,
            bank_socs_pct=(90, 90, 90),
            solar_forecast_kwh=50,
            base_load_power_w=1500,
            ev_soc_pct=100,
            ev_target_soc_pct=100,
            ev_home=True,
            allow_presolar_discharge=False,
        )

        self.assertNotEqual(result.strategy, STRATEGY_CREATE_HEADROOM)
        self.assertEqual(result.recommended_overnight_discharge_kwh, 0.0)

    def test_storm_always_preserves_battery(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=90,
            bank_socs_pct=(90, 90, 90),
            solar_forecast_kwh=50,
            base_load_power_w=1500,
            storm_active=True,
        )

        self.assertEqual(result.strategy, STRATEGY_PRESERVE_FOR_RESILIENCE)
        self.assertEqual(result.recommended_overnight_discharge_kwh, 0.0)


if __name__ == "__main__":
    unittest.main()
