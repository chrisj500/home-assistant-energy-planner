from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from counterfactual import (  # noqa: E402
    advance_counterfactual_ledger,
    counterfactual_bank_socs,
    live_capture_metrics,
)


class CounterfactualTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

    def test_ev_solar_is_added_back_to_no_action_battery(self) -> None:
        ledger = advance_counterfactual_ledger(
            None,
            now=self.now,
            actual_stored_kwh=20.0,
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            charge_efficiency=0.9,
            ev_power_w=7000,
            solar_surplus_w=8000,
        )
        ledger = advance_counterfactual_ledger(
            ledger,
            now=self.now + timedelta(hours=1),
            actual_stored_kwh=20.0,
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            charge_efficiency=0.9,
            ev_power_w=7000,
            solar_surplus_w=8000,
            max_sample_gap_seconds=4000,
        )
        self.assertAlmostEqual(ledger["ev_wall_kwh"], 7.0, places=3)
        self.assertAlmostEqual(ledger["ev_solar_kwh"], 7.0, places=3)
        self.assertAlmostEqual(
            ledger["counterfactual_stored_kwh"],
            26.3,
            places=3,
        )

    def test_counterfactual_overflow_becomes_avoided_export(self) -> None:
        ledger = advance_counterfactual_ledger(
            None,
            now=self.now,
            actual_stored_kwh=47.0,
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            charge_efficiency=0.9,
            ev_power_w=6000,
            solar_surplus_w=8000,
        )
        ledger = advance_counterfactual_ledger(
            ledger,
            now=self.now + timedelta(minutes=30),
            actual_stored_kwh=48.0,
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            charge_efficiency=0.9,
            ev_power_w=6000,
            solar_surplus_w=8000,
            max_sample_gap_seconds=3600,
        )
        self.assertAlmostEqual(ledger["counterfactual_stored_kwh"], 49.152, places=3)
        self.assertGreater(ledger["avoided_export_kwh"], 0.0)

    def test_long_gap_does_not_invent_ev_energy(self) -> None:
        ledger = advance_counterfactual_ledger(
            None,
            now=self.now,
            actual_stored_kwh=20.0,
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            charge_efficiency=0.9,
            ev_power_w=7000,
            solar_surplus_w=9000,
        )
        ledger = advance_counterfactual_ledger(
            ledger,
            now=self.now + timedelta(minutes=20),
            actual_stored_kwh=21.0,
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            charge_efficiency=0.9,
            ev_power_w=7000,
            solar_surplus_w=9000,
        )
        self.assertEqual(ledger["ev_wall_kwh"], 0.0)
        self.assertAlmostEqual(ledger["counterfactual_stored_kwh"], 21.0)

    def test_day_rollover_resets_daily_energy(self) -> None:
        ledger = {
            "date": "2026-09-24",
            "last_at": self.now.isoformat(),
            "last_actual_stored_kwh": 20.0,
            "last_ev_power_w": 7000.0,
            "last_solar_surplus_w": 9000.0,
            "counterfactual_stored_kwh": 30.0,
            "ev_wall_kwh": 12.0,
            "ev_solar_kwh": 12.0,
            "ev_solar_stored_equiv_kwh": 10.8,
            "avoided_export_kwh": 2.0,
        }
        reset = advance_counterfactual_ledger(
            ledger,
            now=self.now,
            actual_stored_kwh=18.0,
            capacity_kwh=49.152,
            charge_limit_pct=100.0,
            charge_efficiency=0.9,
            ev_power_w=0.0,
            solar_surplus_w=0.0,
        )
        self.assertEqual(reset["date"], "2026-09-25")
        self.assertEqual(reset["ev_wall_kwh"], 0.0)
        self.assertEqual(reset["counterfactual_stored_kwh"], 18.0)

    def test_water_fill_preserves_capacity_and_balances_low_banks(self) -> None:
        socs = counterfactual_bank_socs(
            actual_socs_pct=(68.0, 67.0, 80.0),
            capacities_kwh=(18.432, 12.288, 18.432),
            target_stored_kwh=47.55,
            charge_limit_pct=100.0,
        )
        total = sum(
            cap * soc / 100.0
            for cap, soc in zip((18.432, 12.288, 18.432), socs)
        )
        self.assertAlmostEqual(total, 47.55, places=3)
        self.assertLessEqual(max(socs), 100.0)
        self.assertGreater(min(socs), 90.0)

    def test_live_capture_risk_uses_counterfactual_headroom_runway(self) -> None:
        metrics = live_capture_metrics(
            counterfactual_headroom_kwh=11.0,
            solar_surplus_w=8000,
            remaining_daylight_hours=3.5,
            charge_efficiency=0.9,
        )
        self.assertTrue(metrics["risk"])
        self.assertLess(metrics["fill_hours"], 2.0)
        self.assertGreater(metrics["implied_excess_ac_kwh"], 10.0)


if __name__ == "__main__":
    unittest.main()
