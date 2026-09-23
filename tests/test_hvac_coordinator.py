"""Exercise coordinator wiring with HA storage/services replaced by fakes."""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(ROOT))
from hvac import celsius, hourly_forecast, number, observe, room_summary
from hvac_energy import update_energy
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
        self.c._hvac_energy = None
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
        self.states["sensor.hvac_power"] = state(0, unit_of_measurement="W")
        self.states["sensor.ecoflow_smart_home_panel_2_circuit_4_power"] = state(
            10, unit_of_measurement="W"
        )
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
        self.c._hvac_energy_store = SimpleNamespace(async_load=load, async_save=save)
        self.c.hass = SimpleNamespace(states=self.states, services=SimpleNamespace(async_call=call),
            config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°F")))

    def test_live_mapping_and_weather_and_shadow_only(self):
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["hvac_status"], "learning")
        self.assertFalse(result["hvac_diagnostics"]["forecast_applied"])
        self.assertEqual(result["hvac_diagnostics"]["rooms"]["room_count"], 3)
        self.assertIsNone(result["hvac_diagnostics"]["hourly_shadow"][0]["expected_w"])
        self.assertEqual(result["effective_reserve_floor"], 10)

    def test_unchanged_numeric_power_remains_valid(self):
        for entity in (
            "sensor.hvac_power",
            "sensor.ecoflow_smart_home_panel_2_circuit_4_power",
        ):
            self.states[entity].last_reported = self.now - timedelta(hours=2)
            self.states[entity].last_updated = self.now - timedelta(hours=2)
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["hvac_electrical_power_w"], 10)
        self.assertTrue(result["hvac_power_sources"]["condenser"]["available"])
        self.assertEqual(
            result["hvac_power_sources"]["blower_controls"]["reason"],
            "numeric_available",
        )

    def test_unavailable_power_is_not_integrated_as_zero(self):
        self.states["sensor.hvac_power"].state = "unavailable"
        result = asyncio.run(self.c._async_update_data())
        self.assertIsNone(result["hvac_electrical_power_w"])
        self.assertEqual(
            result["hvac_power_sources"]["condenser"]["reason"],
            "state_unavailable",
        )

    def test_legacy_homepod_prefixes_resolve_to_created_entities(self):
        self.c.cfg["hvac_room_prefixes"] = ",".join(
            (
                "sensor.homepod_indoor_climate_living_room",
                "sensor.home_homepod_indoor_climate_guest_bedroom",
                "sensor.home_homepod_indoor_climate_main_bedroom_left",
                "sensor.home_homepod_indoor_climate_main_bedroom_right",
            )
        )
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(
            result["hvac_diagnostics"]["room_prefixes"],
            [
                "sensor.homepod_indoor_climate_living_room",
                "sensor.homepod_indoor_climate_guest_bedroom",
                "sensor.homepod_indoor_climate_main_bedroom_left",
                "sensor.homepod_indoor_climate_main_bedroom_right",
            ],
        )
        self.assertTrue(result["hvac_diagnostics"]["room_data_healthy"])

    def test_canonical_prefixes_resolve_to_persisted_legacy_entities(self):
        for room in ("guest_bedroom", "main_bedroom_left", "main_bedroom_right"):
            canonical = f"sensor.homepod_indoor_climate_{room}"
            legacy = f"sensor.home_homepod_indoor_climate_{room}"
            for suffix in ("_temperature", "_humidity"):
                self.states[legacy + suffix] = self.states.pop(canonical + suffix)

        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(
            result["hvac_diagnostics"]["room_prefixes"],
            [
                "sensor.homepod_indoor_climate_living_room",
                "sensor.home_homepod_indoor_climate_guest_bedroom",
                "sensor.home_homepod_indoor_climate_main_bedroom_left",
                "sensor.home_homepod_indoor_climate_main_bedroom_right",
            ],
        )
        self.assertTrue(result["hvac_diagnostics"]["room_data_healthy"])

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
        self.assertEqual(result["hvac_electrical_power_w"], 10)
        self.assertEqual(result["hvac_daily_electricity_kwh"], 0)
        self.assertTrue(result["rolling_ev_auto_charge_eligible"])
