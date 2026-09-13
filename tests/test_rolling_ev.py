from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from forecast_solar_shadow import IntervalPoint  # noqa: E402
from rolling_ev import (  # noqa: E402
    DaylightWindow,
    choose_ev_charge_window,
    ev_wall_energy_to_target_kwh,
    first_headroom_risk,
    learned_charge_power_w,
    planning_base_load_w,
    simulate_rolling_days,
)
from simulation import ControllerSettings  # noqa: E402


class RollingEvTests(unittest.TestCase):
    def test_planning_base_load_blends_smoothed_non_ev_history(self) -> None:
        value, source = planning_base_load_w(4683.0, 3109.0, 0.0)
        self.assertEqual(source, "blend_3h_24h")
        self.assertAlmostEqual(value or 0.0, 3817.3, places=1)

    def test_planning_base_load_ignores_zero_recent_and_uses_24h(self) -> None:
        value, source = planning_base_load_w(0.0, 3109.0, 0.0)
        self.assertEqual(source, "recent_24h")
        self.assertEqual(value, 3109.0)

    def test_ev_wall_energy_to_target(self) -> None:
        energy = ev_wall_energy_to_target_kwh(
            current_soc_pct=40.0,
            target_soc_pct=80.0,
            wall_kwh_full=20.0,
        )
        self.assertAlmostEqual(energy or 0.0, 8.0)

    def test_learned_charge_power_uses_median_then_fallback(self) -> None:
        power, count = learned_charge_power_w([6400.0, 6600.0, 6800.0], 7200.0)
        self.assertEqual(count, 3)
        self.assertEqual(power, 6600.0)
        fallback, fallback_count = learned_charge_power_w([], 7200.0)
        self.assertEqual(fallback_count, 0)
        self.assertEqual(fallback, 7200.0)

    def test_rolling_days_detect_capacity_headroom_risk(self) -> None:
        start = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)
        sunset = start + timedelta(hours=12)
        points = [
            IntervalPoint(start, 0.0),
            IntervalPoint(start + timedelta(hours=6), 12000.0),
            IntervalPoint(sunset, 0.0),
        ]
        windows = [
            DaylightWindow(day=start.date(), sunrise=start, sunset=sunset),
        ]
        plans = simulate_rolling_days(
            points=points,
            reference=start,
            daylight_windows=windows,
            initial_bank_socs_pct=(90.0, 90.0, 90.0),
            bank_capacities_kwh=(18.432, 12.288, 18.432),
            charge_limit_pct=100.0,
            reserve_pct=10.0,
            charge_efficiency=0.90,
            controller=ControllerSettings(enabled=True, maximum_rate_w=3900.0),
            average_load_kw=2.0,
            overnight_drop_kw=0.0,
            step_minutes=5,
        )
        self.assertEqual(len(plans), 1)
        risk = first_headroom_risk(plans)
        self.assertIsNotNone(risk)
        assert risk is not None
        self.assertGreater(risk.headroom_shortfall_kwh, 0.0)
        self.assertGreater(risk.capacity_export_kwh, 0.0)

    def test_ev_window_prefers_solar_rich_period(self) -> None:
        start = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)
        sunset = start + timedelta(hours=12)
        points = [
            IntervalPoint(start, 0.0),
            IntervalPoint(start + timedelta(hours=4), 2000.0),
            IntervalPoint(start + timedelta(hours=6), 10000.0),
            IntervalPoint(start + timedelta(hours=8), 2000.0),
            IntervalPoint(sunset, 0.0),
        ]
        window = choose_ev_charge_window(
            points=points,
            daylight_windows=[DaylightWindow(start.date(), start, sunset)],
            earliest=start,
            latest=sunset,
            base_load_kw=2.0,
            charge_power_w=6000.0,
            energy_kwh=6.0,
            charge_efficiency=0.90,
            step_minutes=15,
        )
        self.assertIsNotNone(window)
        assert window is not None
        self.assertGreater(window.solar_energy_kwh, 5.0)
        self.assertLess(window.grid_energy_kwh, 1.0)
        self.assertTrue(start + timedelta(hours=5) <= window.start <= start + timedelta(hours=7))


if __name__ == "__main__":
    unittest.main()
