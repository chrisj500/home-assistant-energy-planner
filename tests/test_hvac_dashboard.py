"""Execute the HVAC dashboard template and verify diagnostic wiring."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest
import yaml


class HVACDashboardTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required for Lovelace JS validation")
    def test_template_states(self):
        path = Path(__file__).resolve().parents[1] / "dashboards/energy-planning.yaml"
        raw = path.read_text()
        dashboard = yaml.safe_load(raw)
        card = dashboard["views"][0]["sections"][0]["cards"][1]
        code = card["custom_fields"]["content"].strip()[3:-3]
        script = "const render = new Function('states','hass'," + json.dumps(code) + ");" + r'''
        const hass = {config:{unit_system:{temperature:'°F'}}};
        const missing = render({},hass);
        if (!missing.includes('State unavailable') || !missing.includes('SET TO')) {
          throw Error('Missing-state Aqara face');
        }

        const room = (value) => ({
          state:String(value),
          attributes:{
            unit_of_measurement:'°F',
            fresh:true,
            last_received:new Date().toISOString()
          }
        });

        for (const [action,label] of [
          ['cooling','Cooling'],
          ['heating','Heating'],
          ['fan','Fan only'],
          ['idle','Idle'],
          ['off','Off']
        ]) {
          const states = {
            'climate.thermostat':{
              state:'cool',
              attributes:{
                hvac_action:action,
                current_temperature:72,
                current_humidity:48,
                temperature:73,
                fan_mode:'auto',
                preset_mode:'home',
                temperature_unit:'°F'
              }
            },
            'sensor.energy_planner_hvac_model_status':{
              state:'learning',
              attributes:{
                learning_samples:12,
                learning_samples_required:36,
                learning_days:1,
                learning_days_required:3,
                learning_progress_pct:33.3,
                hourly_supported_hours:0,
                hourly_forecast_hours:24
              }
            },
            'sensor.energy_planner_hvac_electrical_power':{
              state:'10',
              attributes:{
                power_sources:{
                  condenser:{entity_id:'sensor.hvac_power',available:true,raw_state:'0',reason:'numeric_available'},
                  blower_controls:{entity_id:'sensor.blower',available:true,raw_state:'10',reason:'numeric_available'}
                }
              }
            },
            'sensor.energy_planner_hvac_daily_electricity':{
              state:'1.2',
              attributes:{coverage_percent:80,partial:true}
            },
            'sensor.homepod_indoor_climate_living_room_temperature':room(71),
            'sensor.homepod_indoor_climate_guest_bedroom_temperature':room(72),
            'sensor.homepod_indoor_climate_main_bedroom_left_temperature':room(73),
            'sensor.homepod_indoor_climate_main_bedroom_right_temperature':room(73)
          };
          const html = render(states,hass);
          if (!html.includes(label) || !html.includes('SET TO') || !html.includes('73')) {
            throw Error(action + ' thermostat face');
          }
          if (!html.includes('12/36 clean five-minute samples') || !html.includes('1/3 days')) {
            throw Error(action + ' learning progress');
          }
          if (!html.includes('10 W') || !html.includes('1.20 kWh')) {
            throw Error(action + ' electricity');
          }
          if (html.includes('NaN')) throw Error(action + ' NaN');
        }
        '''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

        self.assertNotIn("sensor.home_homepod_indoor_climate_", raw)
        for entity_id in (
            "sensor.homepod_indoor_climate_living_room_temperature",
            "sensor.homepod_indoor_climate_guest_bedroom_temperature",
            "sensor.homepod_indoor_climate_main_bedroom_left_temperature",
            "sensor.homepod_indoor_climate_main_bedroom_right_temperature",
            "sensor.energy_planner_forecast_learning_progress",
            "sensor.energy_planner_storm_safety_status",
        ):
            self.assertIn(entity_id, raw)


if __name__ == "__main__":
    unittest.main()
