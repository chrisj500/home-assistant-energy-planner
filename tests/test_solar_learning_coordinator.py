"""Exercise real coordinator methods with HA services replaced by fakes."""
import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'energy_planner'
sys.path.insert(0, str(ROOT))
from forecast_solar_shadow import interval_points_from_payload, integrate_interval_energy_kwh, weather_rows
from headroom import correct_current_day_points
from solar_learning import finalize, issue, lead_bucket, number, observe, scorecard, sky_bucket


class Parent:
    async def _async_update_data(self):
        return {'projected_sunset_soc': 42, 'rolling_ev_auto_charge_eligible': False}


class SolarCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
        tree = ast.parse((ROOT/'solar_learning_coordinator.py').read_text())
        selected = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.Assign))
                    and not (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_LOGGER' for t in n.targets))]
        ns = {**globals(), 'EnergyPlannerHVACCoordinator': Parent, 'CONF_ACTUAL_SOLAR_POWER': 'power',
              '_LOGGER': logging.getLogger('solar.test'),
              'dt_util': SimpleNamespace(now=lambda: self.now),
              '_solar_window': lambda hass, day: (self.now.replace(hour=6), self.now.replace(hour=18))}
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'solar_learning_coordinator.py', 'exec'), ns)
        self.c = ns['EnergyPlannerSolarLearningCoordinator'].__new__(ns['EnergyPlannerSolarLearningCoordinator'])
        self.c.cfg = {'power': 'sensor.production'}
        self.c._solar_memory = None
        self.c._solar_saved_at = None
        self.states = {}
        def state(entity_id, value, unit):
            self.states[entity_id] = SimpleNamespace(entity_id=entity_id, state=str(value),
                attributes={'unit_of_measurement': unit}, last_updated=self.now, last_reported=self.now)
        state('sensor.production', 2, 'kW')
        state('sensor.gw3000b_solar_radiation', 500, 'W/m²')
        state('sensor.gw3000b_outdoor_temperature', 77, '°F')
        state('sensor.envoy_test_lifetime_energy_production', 5000, 'kWh')
        self.c.hass = SimpleNamespace(states=SimpleNamespace(get=self.states.get, async_all=lambda: list(self.states.values())))
        self.c._forecast_solar_source = lambda: (object(), None)
        self.c._estimate_source_signature = lambda source: {'site': 'test'}
        self.c._estimate_last_success = self.now
        self.c._estimate_payload = {'result': {'watts': {(self.now+timedelta(hours=h)).isoformat(): 2000 for h in range(-1, 30)}}}
        self.c._professional_cache = {}
        self.c._professional_attempts = {}
        self.saved = None
        async def load(): return deepcopy(self.saved)
        async def save(memory): self.saved = deepcopy(memory)
        self.c._solar_learning_store = SimpleNamespace(async_load=load, async_save=save)

    def update(self):
        return asyncio.run(self.c._async_update_data())

    def test_collects_without_weather_and_preserves_operational_decisions(self):
        data = self.update()
        self.assertEqual(data['projected_sunset_soc'], 42)
        self.assertFalse(data['rolling_ev_auto_charge_eligible'])
        d = data['solar_learning_diagnostics']
        self.assertEqual(d['current_observation']['power_w'], 2000)
        self.assertEqual(d['current_observation']['temperature_c'], 25)
        self.assertFalse(d['forecast_applied'])
        self.assertFalse(d['weather_forecast_available'])
        self.assertEqual(len(d['hourly_shadow']), 24)
        self.assertTrue(all(r['start'] > r['issued_at'] for r in self.saved['pending']))
        self.assertEqual(d['sources']['energy'], 'sensor.envoy_test_lifetime_energy_production')

    def test_restart_preserves_predictions_but_drops_continuity(self):
        self.update()
        pending = deepcopy(self.saved['pending'])
        self.c._solar_memory = None
        self.now += timedelta(minutes=2)
        self.update()
        self.assertEqual(self.c._solar_memory['pending'], pending)
        self.assertEqual(self.c._solar_memory['actual_hours'], {})

    def test_stale_production_excluded_and_stale_forecast_not_issued(self):
        self.now += timedelta(hours=3)
        data = self.update()
        self.assertEqual(data['solar_learning_status'], 'forecast_unavailable')
        self.assertFalse(data['solar_learning_diagnostics']['current_observation']['valid'])
        self.assertEqual(self.saved['pending'], [])

    def test_zero_production_in_bright_sun_is_not_training_truth(self):
        self.states['sensor.production'].state = '0'
        self.assertFalse(self.update()['solar_learning_diagnostics']['current_observation']['valid'])

    def test_disabled_does_not_collect(self):
        self.c.cfg['solar_learning_enabled'] = False
        self.assertEqual(self.update()['solar_learning_status'], 'disabled')
        self.assertIsNone(self.saved)

    def test_unknown_units_rejected(self):
        self.states['sensor.production'].attributes['unit_of_measurement'] = 'MW'
        self.assertFalse(self.update()['solar_learning_diagnostics']['current_observation']['valid'])

    def test_future_weather_is_saved_as_issued(self):
        self.c._professional_attempts = {'weather': self.now}
        self.c._professional_cache = {'weather': {'result': [{'datetime': (self.now+timedelta(hours=1)).isoformat(), 'sky': .9, 'temperature': 20}]}}
        self.update()
        row = self.saved['pending'][0]
        self.assertEqual(row['sky_bin'], 'cloudy')
        self.assertEqual(row['forecast_temperature_c'], 20)
        self.c._professional_cache['weather']['result'][0]['sky'] = .1
        self.assertEqual(self.saved['pending'][0]['forecast_sky'], .9)
