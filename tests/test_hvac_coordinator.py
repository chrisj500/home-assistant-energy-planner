"""Exercise coordinator wiring with HA storage/services replaced by fakes."""
import ast
import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(ROOT))
from hvac import celsius, hourly_forecast, number, observe, room_summary
from reliability import suppress_actions


class Parent:
    async def _async_update_data(self):
        return {"rolling_ev_auto_charge_eligible": True, "effective_reserve_floor": 10}


class HVACCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        tree = ast.parse((ROOT / "hvac_coordinator.py").read_text())
        selected = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.Assign))
                    and not (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_LOGGER" for t in n.targets))]
        ns = {**globals(), "EnergyPlannerV025Coordinator": Parent,
              "_LOGGER": logging.getLogger("hvac.test"),
              "dt_util": SimpleNamespace(now=lambda: self.now, parse_datetime=datetime.fromisoformat)}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "hvac_coordinator.py", "exec"), ns)
        cls = ns["EnergyPlannerHVACCoordinator"]
        self.c = cls.__new__(cls)
        self.c.cfg = {"hvac_learning_enabled": True}
        self.c._hvac_memory = None
        self.c._weather_at = None
        self.c._weather_hours = []
        self.c._surplus_since = self.now
        self.states = {}
        def state(value, **attrs):
            return SimpleNamespace(state=str(value), attributes=attrs,
                                   last_updated=self.now, last_reported=self.now)
        self.states["climate.thermostat"] = state("cool", current_temperature=72,
            temperature=72, current_humidity=60, hvac_action="idle")
        self.states["sensor.ecowitt_outdoor_temperature"] = state(80, unit_of_measurement="°F")
        self.states["weather.forecast_home"] = state("sunny", humidity=65, temperature_unit="°F")
        self.states["binary_sensor.homepod_indoor_climate_stale_readings"] = state("off")
        for prefix in ns["ROOM_PREFIXES"]:
            self.states[prefix + "_temperature"] = state(72, unit_of_measurement="°F", fresh=True, last_received=self.now.isoformat())
            self.states[prefix + "_humidity"] = state(60, fresh=True, last_received=self.now.isoformat())
        async def load():
            return {}
        async def save(value):
            self.saved = value
        async def call(*args, **kwargs):
            return {"weather.forecast_home": {"forecast": [{"datetime": self.now.isoformat(), "temperature": 80, "humidity": 65}]}}
        self.c._hvac_store = SimpleNamespace(async_load=load, async_save=save)
        self.c.hass = SimpleNamespace(states=self.states, services=SimpleNamespace(async_call=call),
            config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°F")))
        self.c._fresh_power = lambda entity, now: 0 if entity == "sensor.hvac_power" else 10

    def test_live_mapping_and_weather_and_shadow_only(self):
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["hvac_status"], "learning")
        self.assertFalse(result["hvac_diagnostics"]["forecast_applied"])
        self.assertEqual(result["hvac_diagnostics"]["rooms"]["room_count"], 3)
        self.assertIsNone(result["hvac_diagnostics"]["hourly_shadow"][0]["expected_w"])
        self.assertEqual(result["effective_reserve_floor"], 10)

    def test_stale_room_blocks_existing_advice(self):
        self.states["binary_sensor.homepod_indoor_climate_stale_readings"].state = "on"
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["forecast_reliability_status"], "hvac_hold")
        self.assertFalse(result["rolling_ev_auto_charge_eligible"])
        self.assertEqual(self.saved["samples"], [])

    def test_disabled_does_not_read_or_hold(self):
        self.c.cfg["hvac_learning_enabled"] = False
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["hvac_status"], "disabled")
        self.assertTrue(result["rolling_ev_auto_charge_eligible"])
