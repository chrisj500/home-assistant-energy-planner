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

    def test_ev_parameters_card_is_under_battery_outlook(self):
        outlook = self.sections[2]["cards"]
        self.assertEqual(
            outlook[2].get("entity"),
            "binary_sensor.energy_planner_ev_auto_charge_eligible",
        )
        content = outlook[2]["custom_fields"]["content"]
        for label in [
            "RANGE",
            "RANGE WITH A/C",
            "ENERGY TO TARGET",
            "CHARGE RATE",
            "CURRENT CHARGE",
            "FULL CHARGE ENERGY",
            "Toyota Connected Services",
        ]:
            self.assertIn(label, content)

    def test_ev_card_has_confirmed_remote_start_and_stop_controls(self):
        card = self.sections[2]["cards"][2]
        controls = card["custom_fields"]["controls"]["card"]["cards"]
        by_service = {item["tap_action"]["service"]: item for item in controls}
        self.assertEqual(
            set(by_service),
            {
                "toyota_na.door_lock",
                "toyota_na.door_unlock",
                "toyota_na.engine_start",
                "toyota_na.engine_stop",
                "toyota_na.refresh",
            },
        )
        for service in ("toyota_na.engine_start", "toyota_na.engine_stop"):
            self.assertIn("confirmation", by_service[service]["tap_action"])
        for service in ("toyota_na.door_lock", "toyota_na.door_unlock", "toyota_na.refresh"):
            self.assertNotIn("confirmation", by_service[service]["tap_action"])
        for item in controls:
            self.assertIn("ev_device_id", item["tap_action"]["service_data"]["vehicle"])
            self.assertTrue(item.get("icon", "").startswith("mdi:"))

    @unittest.skipUnless(shutil.which("node"), "Node required for Lovelace JS validation")
    def test_ev_card_uses_selected_vehicle_range_and_handles_missing_range(self):
        card = self.sections[2]["cards"][2]
        code = card["custom_fields"]["content"].strip()[3:-3]
        script = "const render = new Function('states','hass'," + json.dumps(code) + ");" + r'''
        const states = {
          'binary_sensor.energy_planner_ev_auto_charge_eligible':{
            state:'off',attributes:{
              ev_soc_entity:'sensor.selected_ev_soc',ev_home_entity:'device_tracker.ev',
              ev_lock_entity:'lock.selected_vehicle',ev_device_id:'device_123',
              ev_current_power_w:0,learned_charge_power_w:3150,
              learned_charge_power_source:'learned'
            }
          },
          'sensor.selected_ev_soc':{state:'64',attributes:{friendly_name:'Model EV Battery Level Model'}},
          'sensor.energy_planner_ev_soc':{state:'64'},
          'sensor.energy_planner_ev_available_energy_to_target':{state:'5.1'},
          'sensor.energy_planner_ev_learned_full_range_wall_energy':{state:'14.2'},
          'device_tracker.ev':{state:'home'},
          'lock.selected_vehicle':{state:'locked',attributes:{friendly_name:'Model Vehicle'}},
          'sensor.ev_range':{state:'42',attributes:{friendly_name:'Model EV Range Model',unit_of_measurement:'mi'}},
          'sensor.ev_range_ac':{state:'38',attributes:{friendly_name:'Model EV Range AC Model',unit_of_measurement:'mi'}},
          'sensor.other_range':{state:'999',attributes:{friendly_name:'Other EV Range Other',unit_of_measurement:'mi'}}
        };
        let html = render(states,{});
        for (const text of ['64%','42 mi','38 mi','5.1 kWh','3.15 kW','14.2 kWh','AT HOME','LOCKED']) {
          if (!html.includes(text)) throw Error(`EV card is missing ${text}`);
        }
        if (html.includes('999 mi')) throw Error('Card selected another vehicle range');
        delete states['sensor.ev_range'];
        delete states['sensor.ev_range_ac'];
        html = render(states,{});
        if (!html.includes('Unavailable')) throw Error('Missing range was not handled gracefully');
        '''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_battery_card_prefers_planner_weighted_soc(self):
        headroom = self.sections[1]["cards"]
        content = headroom[1]["custom_fields"]["content"]
        planner = content.index("sensor.energy_planner_whole_bank_soc")
        fallback = content.index("sensor.ecoflow_smart_home_panel_2_backup_battery")
        self.assertLess(planner, fallback)

    def test_battery_card_shows_effective_capacity_and_pack_count(self):
        headroom = self.sections[1]["cards"]
        card = headroom[1]
        content = card["custom_fields"]["content"]
        triggers = card["triggers_update"]
        self.assertIn("sensor.energy_planner_effective_battery_capacity", triggers)
        self.assertIn("sensor.energy_planner_battery_pack_count", triggers)
        self.assertIn("sensor.energy_planner_effective_battery_capacity", content)
        self.assertIn("sensor.energy_planner_battery_pack_count", content)
        self.assertIn("kWh", content)
        self.assertIn("packs", content)

    @unittest.skipUnless(shutil.which("node"), "Node required for Lovelace JS validation")
    def test_battery_card_shows_discharge_rate_and_time_to_reserve(self):
        card = self.sections[1]["cards"][1]
        code = card["custom_fields"]["content"].strip()[3:-3]
        script = "const render = new Function('states','hass'," + json.dumps(code) + ");" + r'''
        const states = {
          'sensor.energy_planner_whole_bank_soc':{state:'64'},
          'sensor.energy_planner_effective_battery_capacity':{state:'55.3'},
          'sensor.energy_planner_battery_pack_count':{state:'9'},
          'sensor.energy_planner_battery_topology_source':{state:'panel'},
          'sensor.ecoflow_smart_home_panel_2_ac1_battery':{state:'67'},
          'sensor.ecoflow_smart_home_panel_2_ac2_battery':{state:'67'},
          'sensor.ecoflow_smart_home_panel_2_ac3_battery':{state:'69'},
          'sensor.ecoflow_smart_home_panel_2_ac1_power':{state:'-1325'},
          'sensor.ecoflow_smart_home_panel_2_ac2_power':{state:'-1325'},
          'sensor.ecoflow_smart_home_panel_2_ac3_power':{state:'0'},
          'sensor.patio_ecoflow_smart_home_panel_2_backup_charge_time_remaining':{state:'unknown'},
          'number.ecoflow_smart_home_panel_2_backup_reserve_level':{state:'50'}
        };
        const html = render(states,{});
        for (const text of ['DISCHARGING','DISCHARGE RATE','2.65 kW','≈ 2h 38m to 50% reserve']) {
          if (!html.includes(text)) throw Error(`Missing discharge display: ${text}`);
        }
        if (!html.includes('grid-template-columns:') || !html.includes('minmax(0,1fr) auto')) {
          throw Error('Battery heading does not reserve space for the status indicator');
        }
        '''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node required for Lovelace JS validation")
    def test_battery_discharge_eta_respects_reserve_and_missing_inputs(self):
        card = self.sections[1]["cards"][1]
        code = card["custom_fields"]["content"].strip()[3:-3]
        script = "const render = new Function('states','hass'," + json.dumps(code) + ");" + r'''
        const states = {
          'sensor.energy_planner_whole_bank_soc':{state:'49'},
          'sensor.energy_planner_effective_battery_capacity':{state:'55.3'},
          'sensor.energy_planner_battery_pack_count':{state:'9'},
          'sensor.energy_planner_battery_topology_source':{state:'panel'},
          'sensor.ecoflow_smart_home_panel_2_ac1_battery':{state:'49'},
          'sensor.ecoflow_smart_home_panel_2_ac2_battery':{state:'49'},
          'sensor.ecoflow_smart_home_panel_2_ac3_battery':{state:'49'},
          'sensor.ecoflow_smart_home_panel_2_ac1_power':{state:'-500'},
          'sensor.ecoflow_smart_home_panel_2_ac2_power':{state:'0'},
          'sensor.ecoflow_smart_home_panel_2_ac3_power':{state:'0'},
          'sensor.patio_ecoflow_smart_home_panel_2_backup_charge_time_remaining':{state:'unknown'},
          'number.ecoflow_smart_home_panel_2_backup_reserve_level':{state:'50'}
        };
        let html = render(states,{});
        if (!html.includes('At configured reserve')) throw Error('Reserve reached state was not shown');
        states['sensor.energy_planner_whole_bank_soc'].state = '64';
        states['number.ecoflow_smart_home_panel_2_backup_reserve_level'].state = 'unknown';
        html = render(states,{});
        if (!html.includes('Time to reserve unavailable')) throw Error('Missing reserve was not handled');
        '''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_diagnostics_exposes_battery_topology(self):
        raw = yaml.safe_dump(self.diagnostics)
        self.assertIn("sensor.energy_planner_effective_battery_capacity", raw)
        self.assertIn("sensor.energy_planner_battery_pack_count", raw)
        self.assertIn("sensor.energy_planner_battery_topology_source", raw)

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
    def test_battery_outlook_shows_asymmetric_scenario_range_not_plus_minus(self):
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
          {
            date:'2026-09-30',
            start_soc_pct:20,
            sunset_soc_pct:58.57,
            display_sunset_soc_pct:58.57,
            sunset_soc_low_pct:30.6,
            sunset_soc_high_pct:93.4,
            display_uncertainty_pct:14.7,
            display_confidence:'low'
          }
        ];
        const states = {
          'binary_sensor.energy_planner_dynamic_load_forecast':{state:'off',attributes:{days}},
          'sensor.energy_planner_effective_reserve_floor':{state:'20',attributes:{}},
          'sun.sun':{state:'below_horizon',attributes:{}}
        };
        const html = render(states,{});
        if (!html.includes('31–93% · low')) {
          throw Error('Scenario envelope is not visible in Battery Outlook');
        }
        if (html.includes('±')) {
          throw Error('Symmetric uncertainty notation still shown');
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
        self.assertIn("IMMEDIATE EV CHARGE GATE", content)
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
        self.assertIn("EXPORT RISK", content)
        self.assertIn("sensor.energy_planner_forecast_export_risk_date", content)
        self.assertIn("Lexus is already full", content)
        self.assertIn("high-solar scenario", content)
        self.assertNotIn("kWh of stationary-battery headroom", content)
        self.assertLess(
            content.index("EXPORT RISK"),
            content.index("IMMEDIATE EV CHARGE GATE"),
        )
        self.assertLess(
            content.index("EXPORT RISK"),
            content.index("NO EV ACTION REQUIRED"),
        )
        self.assertIn("does not change the multi-day export outlook", content)

    def test_battery_outlook_displays_export_defense_future_risk(self):
        outlook = self.sections[2]["cards"][1]["custom_fields"]["content"]
        self.assertIn("export_defense_risk", outlook)
        self.assertIn("export_defense_wall_energy_kwh", outlook)
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


    def test_four_day_advice_uses_local_times_and_never_implies_a_command(self):
        view = next(
            view for view in self.dashboard["views"]
            if view["title"] == "Four-Day Plan"
        )
        cards = [
            card
            for section in view.get("sections", [])
            for card in section.get("cards", [])
        ]
        what = next(card for card in cards if card.get("title") == "What To Do")
        content = what["content"]
        self.assertIn("as_local(as_datetime(d.ev_window_start))", content)
        self.assertIn("as_local(as_datetime(d.flex_window_start))", content)
        self.assertNotIn("split('T')", content)
        self.assertIn("does not send a charging command", content)
        self.assertIn("can return during the day", content)

    def test_baseline_export_summary_names_what_is_being_counted(self):
        headroom = self.sections[1]["cards"][2]["content"]
        self.assertIn("Projected baseline export before discretionary loads", headroom)
        self.assertNotIn("Solar after load", headroom)

if __name__ == "__main__":
    unittest.main()
