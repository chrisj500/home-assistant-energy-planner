"""Static guards for Home Assistant diagnostics support."""
from pathlib import Path
import unittest


class DiagnosticsTests(unittest.TestCase):
    def test_downloadable_diagnostics_exposes_live_planner_context(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "energy_planner"
            / "diagnostics.py"
        )
        raw = path.read_text()
        self.assertIn("async_get_config_entry_diagnostics", raw)
        self.assertIn("CONF_FORECAST_SOLAR_API_KEY", raw)
        self.assertIn("async_redact_data", raw)
        self.assertIn('"configured_entities"', raw)
        self.assertIn('"planner_entities"', raw)
        self.assertIn('"coordinator_data"', raw)
        self.assertIn('"internal_status"', raw)
        self.assertIn('"forecast_interval_origin"', raw)
        self.assertIn('"hvac_persistence"', raw)
        self.assertIn('"hvac_memory_counts"', raw)
        self.assertIn('"hvac_thermostat"', raw)


if __name__ == "__main__":
    unittest.main()
