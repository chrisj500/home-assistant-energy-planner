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

    def test_battery_card_prefers_planner_weighted_soc(self):
        headroom = self.sections[1]["cards"]
        content = headroom[1]["custom_fields"]["content"]
        planner = content.index("sensor.energy_planner_whole_bank_soc")
        fallback = content.index("sensor.ecoflow_smart_home_panel_2_backup_battery")
        self.assertLess(planner, fallback)

    def test_battery_outlook_places_uncertainty_below_fill_icon(self):
        outlook = self.sections[2]["cards"][1]["custom_fields"]["content"]
        battery_call = outlook.index("${battery(", outlook.index("const sunsetCell"))
        confidence_block = outlook.index("${esc(confidenceText)}", battery_call)
        self.assertGreater(confidence_block, battery_call)

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

    def test_live_capture_preempts_forecast_hold_in_what_to_do(self):
        what = next(
            section for section in self.sections
            if self._heading(section) == "What To Do"
        )
        content = what["cards"][1]["content"]
        self.assertIn("CAPTURE SOLAR NOW", content)
        self.assertIn("binary_sensor.energy_planner_live_solar_capture_opportunity", content)
        self.assertIn("NO-ACTION HEADROOM RISK", content)
        self.assertLess(
            content.index("CAPTURE SOLAR NOW"),
            content.index("FORECAST HOLD"),
        )

    def test_headroom_summary_includes_counterfactual_ledger(self):
        headroom = self.sections[1]["cards"][2]["content"]
        self.assertIn("No-action battery", headroom)
        self.assertIn("EV solar today", headroom)
        self.assertIn("Avoided export", headroom)

    def test_battery_outlook_uses_export_defense_risk_even_when_status_is_low(self):
        outlook = self.sections[2]["cards"][1]["custom_fields"]["content"]
        self.assertIn("export_defense_risk", outlook)
        self.assertIn("export_defense_wall_energy_kwh", outlook)
        self.assertNotIn("nominal_dynamic_load_needed", outlook)

    def test_future_risk_precedes_no_ev_action_message(self):
        what = next(
            section for section in self.sections
            if self._heading(section) == "What To Do"
        )
        content = what["cards"][1]["content"]
        self.assertIn("FUTURE HEADROOM RISK", content)
        self.assertIn("sensor.energy_planner_forecast_export_risk_date", content)
        self.assertIn("Lexus is already full", content)
        self.assertIn("zero-export forecast", content)
        self.assertLess(
            content.index("FUTURE HEADROOM RISK"),
            content.index("NO EV ACTION REQUIRED"),
        )

    def test_battery_outlook_displays_nominal_future_risk(self):
        outlook = self.sections[2]["cards"][1]["custom_fields"]["content"]
        self.assertIn("nominal_dynamic_load_needed", outlook)
        self.assertIn("nominal_dynamic_load_needed_kwh", outlook)
        self.assertIn("'Risk'", outlook)

    def test_learning_withholds_recommendation(self):
        what = next(
            section for section in self.sections
            if self._heading(section) == "What To Do"
        )
        content = what["cards"][1]["content"]
        self.assertIn("FORECAST LEARNING", content)
        self.assertIn("No forecast-dependent recommendation yet", content)
        self.assertIn("NO EV ACTION REQUIRED", content)


if __name__ == "__main__":
    unittest.main()
