from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from hvac_energy import update_energy


class HVACEnergyTests(unittest.TestCase):
    def test_measured_power_and_gap(self):
        now = datetime(2026, 9, 23, 12, tzinfo=ZoneInfo("America/New_York"))
        memory = {}
        update_energy(memory, now, 1000)
        result = update_energy(memory, now + timedelta(minutes=3), 3000)
        self.assertAlmostEqual(memory["kwh"], .1)
        self.assertTrue(result["partial"])
        update_energy(memory, now + timedelta(minutes=10), 3000)
        self.assertAlmostEqual(memory["kwh"], .1)
        update_energy(memory, now + timedelta(minutes=11), None)
        update_energy(memory, now + timedelta(minutes=12), 3000)
        self.assertAlmostEqual(memory["kwh"], .1)

    def test_local_midnight_split(self):
        now = datetime(2026, 9, 23, 23, 59, tzinfo=ZoneInfo("America/New_York"))
        memory = {}
        update_energy(memory, now, 1000)
        result = update_energy(memory, now + timedelta(minutes=2), 3000)
        self.assertAlmostEqual(memory["kwh"], 2500 / 60 / 1000)
        self.assertEqual(result["day"], "2026-09-24")
        self.assertFalse(result["partial"])

    def test_restart_preserves_total_not_gap(self):
        now = datetime(2026, 9, 23, 12, tzinfo=ZoneInfo("UTC"))
        memory = {}
        update_energy(memory, now, 1000)
        update_energy(memory, now + timedelta(minutes=1), 1000)
        memory.pop("previous")
        update_energy(memory, now + timedelta(minutes=2), 1000)
        self.assertAlmostEqual(memory["kwh"], 1 / 60)

    def test_dst_coverage_uses_elapsed_seconds(self):
        now = datetime(2026, 11, 1, 3, tzinfo=ZoneInfo("America/New_York"))
        memory = {"day": "2026-11-01", "kwh": 1, "covered_seconds": 7200}
        result = update_energy(memory, now, 0)
        self.assertEqual(result["coverage_percent"], 50)
