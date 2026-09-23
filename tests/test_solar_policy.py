from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from solar_policy import migrated_solar_options


class SolarMigrationTests(unittest.TestCase):
    def test_legacy_migration_preserves_other_settings_and_is_idempotent(self):
        data = {"solar_remaining_entity": "sensor.solar_forecast_remaining_today"}
        options = {"capacity_kwh": 49}
        result = migrated_solar_options(data, options)
        self.assertEqual(result, {"capacity_kwh": 49, "solar_remaining_entity": "sensor.energy_production_today_remaining"})
        self.assertIsNone(migrated_solar_options(data, result))
        self.assertEqual(options, {"capacity_kwh": 49})

    def test_explicit_custom_selection_is_preserved(self):
        self.assertIsNone(migrated_solar_options(
            {"solar_remaining_entity": "sensor.solar_forecast_remaining_today"},
            {"solar_remaining_entity": "sensor.other_raw_forecast"},
        ))
