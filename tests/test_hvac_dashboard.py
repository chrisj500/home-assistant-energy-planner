"""Execute the dashboard template for missing and populated entities."""
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
        card = yaml.safe_load(path.read_text())["views"][0]["sections"][0]["cards"][1]
        code = card["custom_fields"]["content"].strip()[3:-3]
        script = "const render = new Function('states','hass'," + json.dumps(code) + ");" + r'''
        const hass = {config:{unit_system:{temperature:'°F'}}};
        if (!render({},hass).includes('State unavailable')) throw Error('Missing state');
        for (const [action,label] of [['cooling','Cooling'],['heating','Heating'],['fan','Fan only'],['idle','Idle'],['off','Off']]) {
          const states = {'climate.thermostat':{state:'cool',attributes:{hvac_action:action,current_temperature:72,temperature:73}}};
          const html = render(states,hass);
          if (!html.includes(label) || !html.includes('72.0') || html.includes('NaN')) throw Error(action);
        }
        '''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
