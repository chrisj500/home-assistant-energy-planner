"""Validate Energy Planning dashboard layout and reliability presentation."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest
import yaml


class DashboardLayoutTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "dashboards/energy-planning.yaml"
        self.raw = path.read_text()
        self.dashboard = yaml.safe_load(self.raw)
        self.sections = self.dashboard["views"][0]["sections"]

    @staticmethod
    def _heading(section):
        for card in section.get("cards", []):
            if card.get("type") == "heading":
                return card.get("heading")
        return None

    def test_battery_bank_is_first_content_card_and_solar_outlook_follows(self):
        headroom = next(
            section for section in self.sections
            if self._heading(section) == "Headroom Decision"
        )
        cards = headroom["cards"]
        self.assertEqual(cards[0]["heading"], "Headroom Decision")
        self.assertEqual(
            cards[1].get("entity"),
            "sensor.ecoflow_smart_home_panel_2_backup_battery",
        )
        solar_index = next(
            i for i, card in enumerate(cards)
            if card.get("type") == "heading"
            and card.get("heading") == "Solar & Battery Outlook"
        )
        reliability_index = next(
            i for i, card in enumerate(cards)
            if card.get("type") == "entities"
            and card.get("title") == "Forecast reliability"
        )
        self.assertEqual(solar_index, 2)
        self.assertGreater(reliability_index, solar_index)
        self.assertFalse(
            any(
                self._heading(section) == "Solar & Battery Outlook"
                for section in self.sections
            )
        )

    @unittest.skipUnless(shutil.which("node"), "Node required for Lovelace JS validation")
    def test_battery_outlook_hides_global_learning_status_from_each_day(self):
        section = next(
            section for section in self.sections
            if self._heading(section) == "Battery Outlook — Next 4 Days"
        )
        card = next(
            card for card in section["cards"]
            if card.get("entity") == "binary_sensor.energy_planner_dynamic_load_forecast"
        )
        code = card["custom_fields"]["content"].strip()[3:-3]
        script = "const render = new Function('states','hass'," + json.dumps(code) + ");" + r'''
        const days = [
          {date:'2026-09-23',status:'learning',start_soc_pct:19,sunset_soc_pct:30,sunset_soc_low_pct:20,sunset_soc_high_pct:37,dynamic_load_needed:false},
          {date:'2026-09-24',status:'learning',start_soc_pct:20,sunset_soc_pct:40,sunset_soc_low_pct:20,sunset_soc_high_pct:56,dynamic_load_needed:false},
          {date:'2026-09-25',status:'learning',start_soc_pct:20,sunset_soc_pct:50,sunset_soc_low_pct:20,sunset_soc_high_pct:87,dynamic_load_needed:false},
          {date:'2026-09-26',status:'learning',start_soc_pct:20,sunset_soc_pct:75,sunset_soc_low_pct:55,sunset_soc_high_pct:100,dynamic_load_needed:false}
        ];
        const states = {
          'binary_sensor.energy_planner_dynamic_load_forecast':{state:'off',attributes:{days}},
          'sensor.energy_planner_effective_reserve_floor':{state:'20',attributes:{}},
          'sun.sun':{state:'below_horizon',attributes:{}}
        };
        const html = render(states,{});
        if (html.toLowerCase().includes('learning')) {
          throw Error('Global learning state leaked into per-day Battery Outlook headers');
        }
        '''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_learning_has_no_false_no_action_verdict(self):
        self.assertIn("FORECAST LEARNING — NO RECOMMENDATION YET", self.raw)
        learning_pos = self.raw.index("FORECAST LEARNING — NO RECOMMENDATION YET")
        no_action_pos = self.raw.index("NO EV ACTION REQUIRED", learning_pos)
        self.assertLess(learning_pos, no_action_pos)

    def test_forecast_guard_is_concise(self):
        headroom = next(
            section for section in self.sections
            if self._heading(section) == "Headroom Decision"
        )
        guard = next(
            card for card in headroom["cards"]
            if card.get("type") == "markdown"
            and "Forecast guard" in card.get("content", "")
        )
        content = guard["content"]
        self.assertIn("Still needed:", content)
        self.assertIn("Storm protection:", content)
        self.assertNotIn("storm_warning_entity", content)
        self.assertNotIn("storm_raw", content)


if __name__ == "__main__":
    unittest.main()
