from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from ev_advisory import (  # noqa: E402
    auto_charge_eligibility,
    classify_ev_charging_outlook,
    ev_soc_data_status,
)


class EvAdvisoryTests(unittest.TestCase):
    def test_green_for_near_term_headroom_risk_and_solar_rich_window(self) -> None:
        outlook = classify_ev_charging_outlook(
            reference_date=date(2026, 9, 13),
            risk_date=date(2026, 9, 14),
            best_solar_fraction=0.994,
        )
        self.assertEqual(outlook.status, "green")
        self.assertEqual(outlook.days_to_risk, 1)

    def test_yellow_when_near_term_risk_has_mixed_window(self) -> None:
        outlook = classify_ev_charging_outlook(
            reference_date=date(2026, 9, 13),
            risk_date=date(2026, 9, 14),
            best_solar_fraction=0.55,
        )
        self.assertEqual(outlook.status, "yellow")

    def test_red_when_no_headroom_risk_and_weak_solar_window(self) -> None:
        outlook = classify_ev_charging_outlook(
            reference_date=date(2026, 9, 13),
            risk_date=None,
            best_solar_fraction=0.25,
        )
        self.assertEqual(outlook.status, "red")

    def test_yellow_when_no_risk_but_mixed_solar_opportunity_exists(self) -> None:
        outlook = classify_ev_charging_outlook(
            reference_date=date(2026, 9, 13),
            risk_date=None,
            best_solar_fraction=0.70,
        )
        self.assertEqual(outlook.status, "yellow")

    def test_soc_status_marks_stale_telemetry_while_charging(self) -> None:
        self.assertEqual(ev_soc_data_status(45.0, charging=True), "charging_soc_stale")
        self.assertEqual(ev_soc_data_status(10.0, charging=True), "fresh")

    def test_auto_charge_requires_green_active_solar_window(self) -> None:
        now = datetime(2026, 9, 14, 16, 45, tzinfo=timezone.utc)
        eligible, reason = auto_charge_eligibility(
            outlook_status="green",
            now=now,
            window_start=now - timedelta(minutes=10),
            window_end=now + timedelta(minutes=45),
            ev_home=True,
            available_energy_kwh=8.6,
            solar_fraction=0.99,
        )
        self.assertTrue(eligible)
        self.assertIn("active", reason)

        waiting, _ = auto_charge_eligibility(
            outlook_status="green",
            now=now,
            window_start=now + timedelta(hours=1),
            window_end=now + timedelta(hours=2),
            ev_home=True,
            available_energy_kwh=8.6,
            solar_fraction=0.99,
        )
        self.assertFalse(waiting)


if __name__ == "__main__":
    unittest.main()
