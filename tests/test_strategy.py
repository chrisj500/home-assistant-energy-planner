from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from strategy import (  # noqa: E402
    STRATEGY_CREATE_HEADROOM,
    STRATEGY_HOLD,
    STRATEGY_PRESERVE_FOR_RESILIENCE,
    STRATEGY_USE_DISCRETIONARY_LOADS,
    plan_solar_period,
)


class SolarPeriodStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sunrise = datetime(2026, 9, 13, 10, 50, tzinfo=timezone.utc)
        self.sunset = datetime(2026, 9, 13, 23, 15, tzinfo=timezone.utc)
        self.peak = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
        self.common = {
            "sunrise": self.sunrise,
            "sunset": self.sunset,
            "capacity_kwh": 49.152,
            "charge_limit_pct": 100,
            "minimum_reserve_pct": 10,
            "peak_time": self.peak,
            "harvest_capture_factor": 0.88,
            "charge_efficiency": 0.90,
        }

    def test_sep12_evening_case_holds_stored_solar(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=21.75,
            solar_forecast_kwh=21.93,
            base_load_power_w=2800,
            ev_soc_pct=93,
            ev_target_soc_pct=100,
            ev_home=True,
        )

        self.assertEqual(result.strategy, STRATEGY_HOLD)
        self.assertEqual(result.recommended_overnight_discharge_kwh, 0.0)
        self.assertGreater(result.headroom_margin_kwh, 30.0)
        self.assertLess(result.projected_max_soc_pct, 30.0)
        self.assertLess(result.predicted_export_kwh, 1.0)

    def test_big_solar_with_enough_headroom_still_holds(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=50,
            solar_forecast_kwh=35,
            base_load_power_w=1800,
        )

        self.assertEqual(result.strategy, STRATEGY_HOLD)
        self.assertGreater(result.headroom_margin_kwh, 0.0)
        self.assertEqual(result.recommended_overnight_discharge_kwh, 0.0)
        self.assertLess(result.projected_max_soc_pct, 100.0)

    def test_export_risk_prefers_ev_before_stationary_battery_cycle(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=90,
            solar_forecast_kwh=50,
            base_load_power_w=1500,
            ev_soc_pct=40,
            ev_target_soc_pct=100,
            ev_home=True,
        )

        self.assertEqual(result.strategy, STRATEGY_USE_DISCRETIONARY_LOADS)
        self.assertGreater(result.predicted_export_kwh, 1.0)
        self.assertGreater(result.headroom_shortfall_kwh, 1.0)
        self.assertEqual(result.recommended_overnight_discharge_kwh, 0.0)

    def test_export_risk_without_ev_recommends_only_missing_headroom(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=90,
            solar_forecast_kwh=50,
            base_load_power_w=1500,
            ev_soc_pct=100,
            ev_target_soc_pct=100,
            ev_home=True,
        )

        self.assertEqual(result.strategy, STRATEGY_CREATE_HEADROOM)
        self.assertGreater(result.headroom_shortfall_kwh, 1.0)
        self.assertAlmostEqual(
            result.recommended_overnight_discharge_kwh,
            result.headroom_shortfall_kwh,
            places=6,
        )
        ending_soc = (
            90.0
            - 100.0
            * result.recommended_overnight_discharge_kwh
            / self.common["capacity_kwh"]
        )
        self.assertGreaterEqual(ending_soc, self.common["minimum_reserve_pct"])

    def test_storm_always_preserves_battery(self) -> None:
        result = plan_solar_period(
            **self.common,
            current_soc_pct=90,
            solar_forecast_kwh=50,
            base_load_power_w=1500,
            storm_active=True,
        )

        self.assertEqual(result.strategy, STRATEGY_PRESERVE_FOR_RESILIENCE)
        self.assertEqual(result.recommended_overnight_discharge_kwh, 0.0)


if __name__ == "__main__":
    unittest.main()
