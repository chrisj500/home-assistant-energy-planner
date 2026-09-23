"""Execute the HVAC dashboard template and verify room/thermostat wiring."""
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
        if (!missing.includes('State unavailable') || !missing.includes('THERMOSTAT')) {
          throw Error('Missing-state classic thermostat face');
        }
        if (missing.includes('SET TO')) throw Error('Aqara face was not reverted');

        const room = (value, unit) => ({
          state:String(value),
          attributes:{
            unit_of_measurement:unit,
            fresh:true,
            last_received:new Date().toISOString()
          }
        });

        const baseStates = {
          'climate.thermostat':{
            state:'cool',
            attributes:{
              current_temperature:72,
              current_humidity:48,
              temperature:73,
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
              hourly_forecast_hours:24,
              entities:{hvac_outdoor_temperature:'sensor.ecowitt_outdoor_temperature',hvac_weather:'weather.forecast_home'},
              recovery:{
                active:true,
                status:'provisional',
                eta_minutes:18,
                rate_c_per_hour:1,
                completed_cycles:1
              },
              thermal:{
                status:'provisional',
                predicted_drift_c_per_hour:-0.5,
                time_constant_hours:18.5,
                samples:1,
                samples_required:3
              }
            }
          },
          'sensor.ecowitt_outdoor_temperature':{
            state:'80',
            attributes:{unit_of_measurement:'°F'}
          },
          'weather.forecast_home':{
            state:'sunny',
            attributes:{humidity:62}
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
          // Living Room kept the canonical ID in the real system.
          'sensor.homepod_indoor_climate_living_room_temperature':room(71,'°F'),
          'sensor.homepod_indoor_climate_living_room_humidity':room(45,'%'),
          // Existing HA entity-registry IDs for the other rooms may retain the older prefix.
          'sensor.home_homepod_indoor_climate_guest_bedroom_temperature':room(72,'°F'),
          'sensor.home_homepod_indoor_climate_guest_bedroom_humidity':room(50,'%'),
          'sensor.home_homepod_indoor_climate_main_bedroom_left_temperature':room(74,'°F'),
          'sensor.home_homepod_indoor_climate_main_bedroom_left_humidity':room(52,'%'),
          'sensor.home_homepod_indoor_climate_main_bedroom_right_temperature':room(76,'°F'),
          'sensor.home_homepod_indoor_climate_main_bedroom_right_humidity':room(54,'%')
        };

        const fallback = render(baseStates,hass);
        if (!fallback.includes('Cool mode') || fallback.includes('State unavailable')) {
          throw Error('Missing hvac_action did not fall back to thermostat mode');
        }
        if (!fallback.includes('Main bedroom') || !fallback.includes('75.0°F') || !fallback.includes('53.0% RH')) {
          throw Error('Main bedroom left/right average');
        }
        if (!fallback.includes('Guest bedroom') || !fallback.includes('72.0°F')) {
          throw Error('Guest bedroom placement');
        }
        if (!fallback.includes('Living room') || !fallback.includes('71.0°F')) {
          throw Error('Living room placement');
        }
        if (!fallback.includes('House average') || !fallback.includes('72.7°F') || !fallback.includes('49.3% RH')) {
          throw Error('House physical-room average');
        }
        if (!fallback.includes('12/36 clean five-minute samples') || !fallback.includes('1/3 days')) {
          throw Error('Learning progress');
        }
        if (!fallback.includes('10 W') || !fallback.includes('1.20 kWh')) {
          throw Error('Electricity');
        }
        if (!fallback.includes('Outside 80.0°F') || !fallback.includes('Target 73.0°F') || !fallback.includes('Δ +7.0°F')) {
          throw Error('Outdoor temperature/setpoint delta');
        }
        if (!fallback.includes('Outside RH 62.0%') || !fallback.includes('Inside RH 48.0%') || !fallback.includes('Δ +14.0 pts')) {
          throw Error('Outdoor/inside humidity context');
        }
        if (!fallback.includes('Estimated cost today $0.30 @ $0.25/kWh')) {
          throw Error('HVAC import cost');
        }
        const contextPos = fallback.indexOf('Outside 80.0°F');
        const roomPos = fallback.indexOf('Main bedroom');
        if (!(contextPos > fallback.indexOf('THERMOSTAT') && contextPos < roomPos)) {
          throw Error('Outdoor context is not between thermostat and room grid');
        }
        if (!fallback.includes('Recovery ETA 18 min') || !fallback.includes('1.8°F/h')) {
          throw Error('Recovery estimate');
        }
        if (!fallback.includes('Passive thermal drift -0.9°F/h') || !fallback.includes('τ 18.5 h')) {
          throw Error('Thermal drift estimate');
        }

        for (const [action,label] of [
          ['cooling','Cooling'],
          ['heating','Heating'],
          ['fan','Fan only'],
          ['idle','Idle'],
          ['off','Off']
        ]) {
          const states = JSON.parse(JSON.stringify(baseStates));
          states['climate.thermostat'].attributes.hvac_action = action;
          const html = render(states,hass);
          if (!html.includes(label) || html.includes('NaN')) throw Error(action);
        }
        '''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

        self.assertIn("sensor.home_homepod_indoor_climate_", raw)
        self.assertIn("sensor.homepod_indoor_climate_", raw)
        self.assertIn("roomCard('Main bedroom'", raw)
        self.assertIn("roomCard('Guest bedroom'", raw)
        self.assertIn("roomCard('Living room'", raw)
        self.assertIn("roomCard('House average'", raw)
        self.assertIn("sensor.energy_planner_forecast_learning_progress", raw)
        self.assertIn("sensor.energy_planner_storm_safety_status", raw)


if __name__ == "__main__":
    unittest.main()
