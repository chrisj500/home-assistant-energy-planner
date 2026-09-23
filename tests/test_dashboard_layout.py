"""Validate compact Energy Planning overview and diagnostics layout."""
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
        self.overview = self.dashboard["views"][0]
        self.diagnostics = self.dashboard["views"][1]
        self.sections = self.overview["sections"]

    @staticmethod
    def _heading(section):
        for card in section.get("cards", []):
            if card.get("type") == "heading":
                return card.get("heading")
        return None

    def test_overview_is_four_compact_operating_columns(self):
        self.assertEqual(self.overview["title"], "Energy Planning")
        headings = [self._heading(section) for section in self.sections]
        self.assertEqual(
            headings,
            [
                "HVAC Overview",
                "Headroom Decision",
                "Battery Outlook — Next 4 Days",
                "What To Do",
            ],
        )
        self.assertTrue(self.overview.get("dense_section_placement"))
        self.assertTrue(all(len(section.get("cards", [])) <= 3 for section in self.sections))

    def test_top_operating_cards_are_thermostat_battery_and_outlook(self):
        hvac = self.sections[0]["cards"]
        headroom = self.sections[1]["cards"]
        outlook = self.sections[2]["cards"]
        self.assertEqual(hvac[1].get("entity"), "climate.thermostat")
        self.assertEqual(
            headroom[1].get("entity"),
            "sensor.ecoflow_smart_home_panel_2_backup_battery",
        )
        self.assertEqual(
            outlook[1].get("entity"),
            "binary_sensor.energy_planner_dynamic_load_forecast",
        )
        self.assertIn("Solar & Battery Outlook", headroom[2].get("content", ""))

    def test_detailed_diagnostics_are_off_the_operating_view(self):
        self.assertEqual(self.diagnostics["title"], "Diagnostics")
        headings = [
            self._heading(section) for section in self.diagnostics["sections"]
        ]
        self.assertEqual(
            headings,
            ["Planner Health", "HVAC Diagnostics", "Forecast Quality", "Tools"],
        )
        overview_raw = yaml.safe_dump(self.overview)
        diagnostics_cards = [
            card
            for section in self.diagnostics["sections"]
            for card in section.get("cards", [])
        ]
        diagnostics_text = "\n".join(
            str(card.get("title", ""))
            + "\n"
            + str(card.get("name", ""))
            + "\n"
            + str(card.get("content", ""))
            for card in diagnostics_cards
        )
        self.assertNotIn("Forecast reliability", overview_raw)
        self.assertNotIn("Energy History Export", overview_raw)
        self.assertIn("Forecast reliability", diagnostics_text)
        self.assertIn("Energy History Export", diagnostics_text)
        self.assertIn("Battery model", diagnostics_text)
        self.assertIn("Learning persistence", diagnostics_text)

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

    @unittest.skipUnless(shutil.which("node"), "Node required for Lovelace JS validation")
    def test_battery_outlook_shows_explicit_unavailable_reason(self):
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
        const states = {
          'binary_sensor.energy_planner_dynamic_load_forecast':{
            state:'off',
            attributes:{
              days:[],
              battery_outlook_status:'unavailable',
              battery_outlook_reason:'Interval solar forecast unavailable'
            }
          },
          'sensor.energy_planner_effective_reserve_floor':{state:'20',attributes:{}},
          'sun.sun':{state:'above_horizon',attributes:{}}
        };
        const html = render(states,{});
        if (!html.includes('Battery outlook unavailable')) {
          throw Error('Missing battery outlook unavailable heading');
        }
        if (!html.includes('Interval solar forecast unavailable')) {
          throw Error('Missing battery outlook reason');
        }
        '''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_learning_withholds_recommendation(self):
        what = next(
            section for section in self.sections
            if self._heading(section) == "What To Do"
        )
        content = what["cards"][1]["content"]
        self.assertIn("FORECAST LEARNING", content)
        self.assertIn("No recommendation yet", content)
        self.assertIn("NO EV ACTION REQUIRED", content)


if __name__ == "__main__":
    unittest.main()
