from __future__ import annotations

from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from dashboard_model import select_next_sunset_forecast  # noqa: E402


class NextSunsetForecastTests(unittest.TestCase):
    def test_daylight_prefers_forecast_solar_shadow(self) -> None:
        result = select_next_sunset_forecast(
            phase="daylight",
            capacity_kwh=49.152,
            data={
                "today_plan_date": "2026-09-14",
                "forecast_solar_enhancement_status": "shadow_active",
                "forecast_solar_shadow_projected_sunset_soc": 78.5,
                "forecast_solar_shadow_charge_to_sunset": 12.3,
                "forecast_solar_shadow_grid_import_today": 1.2,
                "forecast_solar_shadow_export_today": 0.4,
                "projected_sunset_soc": 75.0,
            },
        )
        self.assertEqual(result.date, "2026-09-14")
        self.assertEqual(result.soc_pct, 78.5)
        self.assertEqual(result.source, "forecast_solar_scaled_live")

    def test_before_sunrise_uses_today_plan_not_current_soc(self) -> None:
        result = select_next_sunset_forecast(
            phase="before_sunrise",
            capacity_kwh=49.152,
            data={
                "today_plan_date": "2026-09-14",
                "weighted_soc": 16.1,
                "today_recommended_presolar_discharge": 0.0,
                "today_projected_sunset_soc": 91.0,
                "today_predicted_grid_import": 5.0,
                "today_predicted_export": 0.0,
            },
        )
        self.assertEqual(result.soc_pct, 91.0)
        self.assertGreater(result.expected_charge_kwh or 0.0, 30.0)
        self.assertEqual(result.source, "configured_day_ahead_today")

    def test_after_sunset_rolls_immediately_to_next_day(self) -> None:
        result = select_next_sunset_forecast(
            phase="after_sunset",
            capacity_kwh=49.152,
            data={
                "next_day_plan_date": "2026-09-15",
                "planned_next_day_start_soc": 22.0,
                "projected_sunset_soc_tomorrow": 88.0,
                "predicted_grid_import_tomorrow": 3.5,
                "predicted_export_tomorrow": 1.25,
                "weighted_soc": 17.0,
            },
        )
        self.assertEqual(result.date, "2026-09-15")
        self.assertEqual(result.soc_pct, 88.0)
        self.assertEqual(result.expected_export_kwh, 1.25)
        self.assertEqual(result.source, "configured_day_ahead_next_day")

    def test_missing_plan_does_not_mirror_current_soc(self) -> None:
        result = select_next_sunset_forecast(
            phase="after_sunset",
            capacity_kwh=49.152,
            data={
                "next_day_plan_date": "2026-09-15",
                "weighted_soc": 16.1,
            },
        )
        self.assertIsNone(result.soc_pct)
        self.assertIsNone(result.expected_charge_kwh)


if __name__ == "__main__":
    unittest.main()
