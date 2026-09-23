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
from hvac import celsius, effective_action, hourly_forecast, number, observe, room_summary
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

    def test_missing_hvac_action_uses_mode_and_power(self):
        self.states["climate.thermostat"].attributes.pop("hvac_action", None)
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["hvac_status"], "learning")
        self.assertIsNone(result["hvac_diagnostics"]["thermostat_action_reported"])
        self.assertEqual(
            result["hvac_diagnostics"]["thermostat_action_effective"],
            "idle",
        )
        self.assertEqual(
            result["hvac_diagnostics"]["thermostat_action_source"],
            "inferred_power",
        )

    def test_missing_hvac_action_detects_active_cooling_from_condenser(self):
        self.states["climate.thermostat"].attributes.pop("hvac_action", None)
        self.states["sensor.hvac_power"].state = "2200"
        self.states["sensor.ecoflow_smart_home_panel_2_circuit_4_power"].state = "150"
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(
            result["hvac_diagnostics"]["thermostat_action_effective"],
            "cooling",
        )
        self.assertEqual(
            result["hvac_diagnostics"]["thermostat_action_source"],
            "inferred_power",
        )

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

    def test_unchanged_thermostat_and_outdoor_readings_remain_valid(self):
        for entity in ("climate.thermostat", "sensor.ecowitt_outdoor_temperature"):
            self.states[entity].last_reported -= timedelta(hours=2)
            self.states[entity].last_updated -= timedelta(hours=2)
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["hvac_status"], "learning")
        details = result["hvac_diagnostics"]["input_states"]["hvac_thermostat"]
        self.assertEqual(details["raw_state"], "cool")
        self.assertEqual(details["report_age_minutes"], 120)
        self.assertTrue(details["available"])

    def test_unknown_thermostat_still_blocks_hvac_advice(self):
        self.states["climate.thermostat"].state = "unavailable"
        result = asyncio.run(self.c._async_update_data())
        self.assertEqual(result["hvac_status"], "unavailable")
        self.assertEqual(result["forecast_reliability_status"], "hvac_hold")
        self.assertFalse(result["hvac_diagnostics"]["input_states"]["hvac_thermostat"]["available"])

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

    def _persisted_hvac_rows(self, count=26):
        base = self.now.timestamp() - 2 * 86400
        rows = []
        for i in range(count):
            at = base + i * 300
            rows.append(
                {
                    "at": at,
                    "day": datetime.fromtimestamp(
                        at, timezone.utc
                    ).date().isoformat(),
                    "mode": "cool",
                    "action": "idle",
                    "target_c": 22.222222,
                    "indoor_c": 22.222222,
                    "outdoor_c": 26.666667,
                    "outdoor_humidity": 65,
                    "humidity": 60,
                    "condenser_w": 0,
                    "blower_w": 10,
                    "response_c_per_hour": 0,
                }
            )
        return rows

    def test_restart_alias_resolution_preserves_hvac_learning_history(self):
        identity = self.c._model_identity(self.c._room_prefixes())
        stored_identity = dict(identity)
        stored_identity["room_prefixes"] = ",".join(
            (
                "sensor.homepod_indoor_climate_living_room",
                "sensor.home_homepod_indoor_climate_guest_bedroom",
                "sensor.home_homepod_indoor_climate_main_bedroom_left",
                "sensor.home_homepod_indoor_climate_main_bedroom_right",
            )
        )
        persisted = {
            "entity_mapping": stored_identity,
            "samples": self._persisted_hvac_rows(),
            "recovery_cycles": [
                {
                    "ended_at": self.now.timestamp() - 3600,
                    "action": "cooling",
                    "rate_c_per_hour": 1.2,
                    "outdoor_delta_c": 5,
                    "duration_minutes": 30,
                }
            ],
            "thermal_samples": [
                {
                    "ended_at": self.now.timestamp() - 3600,
                    "coefficient_per_hour": 0.05,
                    "observed_drift_c_per_hour": -0.25,
                    "duration_minutes": 60,
                    "mean_outdoor_delta_c": 5,
                }
            ],
            "previous": {"at": self.now.timestamp() - 300},
            "call": {"at": self.now.timestamp() - 1800, "temperature": 24},
            "missing_since": self.now.timestamp() - 600,
            "recovery_call": {
                "at": self.now.timestamp() - 900,
                "action": "cooling",
                "indoor_c": 24,
                "target_c": 22,
                "outdoor_c": 28,
            },
            "thermal_window": {
                "at": self.now.timestamp() - 3600,
                "indoor_c": 22,
                "last_at": self.now.timestamp() - 300,
                "last_indoor_c": 21.8,
                "outdoor_sum": 70,
                "outdoor_count": 7,
            },
        }

        async def load_hvac():
            return persisted

        self.c._hvac_store = SimpleNamespace(
            async_load=load_hvac,
            async_save=self.c._hvac_store.async_save,
        )
        result = asyncio.run(self.c._async_update_data())

        self.assertEqual(len(self.saved["samples"]), 26)
        self.assertEqual(len(self.saved["recovery_cycles"]), 1)
        self.assertEqual(len(self.saved["thermal_samples"]), 1)
        self.assertNotIn("recovery_call", self.saved)
        self.assertEqual(
            result["hvac_diagnostics"]["persistence"]["status"],
            "restored",
        )
        self.assertEqual(
            result["hvac_diagnostics"]["persistence"]["restored_samples"],
            26,
        )
        self.assertEqual(
            self.saved["entity_mapping"]["room_prefixes"],
            ",".join(self.c._room_prefixes()),
        )

    def test_store_without_identity_adopts_mapping_without_erasing_history(self):
        persisted = {
            "samples": self._persisted_hvac_rows(12),
            "recovery_cycles": [],
            "thermal_samples": [],
        }

        async def load_hvac():
            return persisted

        self.c._hvac_store = SimpleNamespace(
            async_load=load_hvac,
            async_save=self.c._hvac_store.async_save,
        )
        result = asyncio.run(self.c._async_update_data())

        self.assertEqual(len(self.saved["samples"]), 12)
        self.assertEqual(
            result["hvac_diagnostics"]["persistence"]["status"],
            "restored_identity_adopted",
        )

    def test_material_hvac_mapping_change_resets_incompatible_history(self):
        identity = self.c._model_identity(self.c._room_prefixes())
        stored_identity = dict(identity)
        stored_identity["hvac_condenser"] = "sensor.old_condenser_power"
        persisted = {
            "entity_mapping": stored_identity,
            "samples": self._persisted_hvac_rows(),
            "recovery_cycles": [],
            "thermal_samples": [],
        }

        async def load_hvac():
            return persisted

        self.c._hvac_store = SimpleNamespace(
            async_load=load_hvac,
            async_save=self.c._hvac_store.async_save,
        )
        result = asyncio.run(self.c._async_update_data())

        self.assertEqual(self.saved["samples"], [])
        persistence = result["hvac_diagnostics"]["persistence"]
        self.assertEqual(persistence["status"], "reset_mapping_changed")
        self.assertEqual(persistence["restored_samples"], 0)
        self.assertEqual(
            persistence["reset_reason"]["stored"]["hvac_condenser"],
            "sensor.old_condenser_power",
        )

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
