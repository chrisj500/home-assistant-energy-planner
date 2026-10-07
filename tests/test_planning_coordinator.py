import ast
import importlib.util
import asyncio
from copy import deepcopy
from datetime import datetime,timedelta
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'custom_components'/'energy_planner'))
from test_planning import scenario
from planning import build_plan,publish_plan
from planning_solar import selected_curve
from forecast_solar_shadow import interval_points_from_payload,weather_rows
from headroom import correct_current_day_points
from rolling_ev import DaylightWindow
from const import *


class Parent:
    async def _async_update_data(self):return deepcopy(self.baseline)


class CoordinatorOutcomeTests(unittest.TestCase):
    def setUp(self):
        args=scenario();self.now=args['now'];self.args=args
        tree=ast.parse((ROOT/'custom_components/energy_planner/planning_coordinator.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef))
        ns={**globals(),'EnergyPlannerSolarLearningCoordinator':Parent,
            'dt_util':SimpleNamespace(now=lambda:self.now,parse_datetime=lambda s:datetime.fromisoformat(s) if s else None),
            '_LOGGER':logging.getLogger('test.planning'),
            '_solar_window':lambda hass,day:next((w.sunrise,w.sunset) for w in args['windows'] if w.day==day),
            '_controller_settings':lambda *a:SimpleNamespace(enabled=True,maximum_rate_w=3900),
            '_num':lambda *a:100}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),'planning_coordinator.py','exec'),ns)
        self.c=ns['EnergyPlannerPlanningCoordinator'].__new__(ns['EnergyPlannerPlanningCoordinator'])
        self.c.cfg={};self.c._solar_memory={};self.c._professional_attempts={};self.c._professional_cache={}
        self.c._estimate_last_success=self.now
        self.c._estimate_payload={'result':{'watts':{p.at.isoformat():p.watts for p in args['points']}}}
        self.c.hass=SimpleNamespace(config=SimpleNamespace(latitude=38.9,longitude=-77),states=SimpleNamespace(get=lambda _:None))
        self.c._fresh_power=lambda *a:None
        self.c.baseline={'storm':False,'forecast_reliability_status':'unstable','rolling_ev_status':'hold',
            'rolling_planning_base_load_w':1000,'rolling_planning_load_profile_w':[
                {'date':w.day.isoformat(),'load_w':1000} for w in args['windows']],
            'battery_bank_capacities_kwh':args['capacities'],'battery_bank_socs_pct':args['socs'],
            'effective_reserve_floor':40,'rolling_ev_soc_data_status':'fresh',
            'rolling_ev_available_energy_kwh':1.68,'rolling_ev_charge_power_w':6190,
            'rolling_ev_wall_full_kwh':14}

    def test_real_publication_replaces_legacy_hold_and_retains_all_four_days(self):
        d=asyncio.run(self.c._async_update_data())
        self.assertEqual(d['four_day_plan_status'],'ready')
        self.assertEqual(d['forecast_reliability_status'],'ready')
        self.assertEqual(d['rolling_dynamic_load_days_count'],4)
        self.assertEqual(d['rolling_ev_status'],'planned')
        self.assertAlmostEqual(d['rolling_ev_recommended_energy_kwh'],1.68)
        self.assertEqual(d['next_sunset_expected_export'],d['four_day_plan']['days'][0]['residual_export_kwh'])
        self.assertFalse(d['rolling_ev_auto_charge_eligible'])

    def test_missing_fourth_day_keeps_three_day_plan(self):
        last=self.args['windows'][2].sunset
        self.c._estimate_payload['result']['watts']={p.at.isoformat():p.watts for p in self.args['points'] if p.at<=last}
        d=asyncio.run(self.c._async_update_data())
        self.assertEqual(d['four_day_plan_status'],'ready')
        self.assertEqual(len(d['four_day_plan']['days']),3)
        self.assertEqual(d['four_day_plan']['missing_days'],['2026-10-10'])

    def test_dashboard_uses_only_authoritative_plan_for_recommendations(self):
        import yaml
        x=yaml.safe_load((ROOT/'dashboards/energy-planning-four-day.yaml').read_text())
        sections=x['views'][0]['sections']
        cards=[sections[1]['cards'][-1],sections[2]['cards'][1],sections[3]['cards'][1]]
        for card in cards:
            self.assertIn('sensor.energy_planner_four_day_plan',card['content'])
            self.assertNotIn('forecast_confidence',card['content'])
        self.assertIn('ev_return_opportunity_kwh',cards[-1]['content'])

    def test_stale_provider_has_specific_reason_and_clears_eligibility(self):
        self.c._estimate_last_success=self.now-timedelta(hours=4)
        d=asyncio.run(self.c._async_update_data())
        self.assertEqual(d['four_day_plan_status'],'unavailable')
        self.assertIn('older than two hours',d['four_day_plan']['reason'])
        self.assertFalse(d['rolling_ev_auto_charge_eligible'])


class DashboardRenderTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("jinja2"), "Jinja2 is installed by the CI validation workflow")
    def test_real_templates_render_all_days_and_small_ev_capacity(self):
        import yaml
        from jinja2 import Environment
        p=build_plan(**scenario())
        env=Environment()
        env.globals.update(states=lambda _:p['status'],state_attr=lambda _,key:p.get(key))
        x=yaml.safe_load((ROOT/'dashboards/energy-planning-four-day.yaml').read_text())
        sections=x['views'][0]['sections']
        rendered=[]
        for card in (sections[1]['cards'][-1],sections[2]['cards'][1],sections[3]['cards'][1]):
            rendered.append(env.from_string(card['content']).render())
        for day in p['days']:
            self.assertIn(day['date'],rendered[2])
        self.assertIn('Charge the EV 1.7 kWh',rendered[2])
        self.assertNotIn('unstable',rendered[2])
