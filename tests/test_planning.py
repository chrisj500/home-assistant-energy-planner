"""Outcome tests for the export-first four-day planner."""
import unittest
import math
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'custom_components'/'energy_planner'))
from planning import build_plan,publish_plan
from rolling_ev import DaylightWindow
from forecast_solar_shadow import IntervalPoint
from planning_solar import selected_curve


def scenario(solar_kw=10, load_kw=1, soc=99, reserve=40, ev=1.68, now_hour=2, days=4):
    now=datetime(2026,10,7,now_hour,tzinfo=timezone.utc)
    windows=[];points=[];loads={}
    for i in range(days):
        day=now+timedelta(days=i)
        sunrise=day.replace(hour=6);sunset=day.replace(hour=18)
        windows.append(DaylightWindow(day.date(),sunrise,sunset))
        loads[day.date().isoformat()]=load_kw
        for h in range(25):
            at=day.replace(hour=0)+timedelta(hours=h)
            points.append(IntervalPoint(at,solar_kw*1000*max(math.sin(math.pi*(h-6)/12),0) if 6<=h<=18 else 0))
    points=list({p.at:p for p in points}.values()); points.sort(key=lambda p:p.at)
    return dict(now=now,windows=windows,points=points,capacities=[18.432]*3,socs=[soc]*3,
                load_by_day=loads,reserve_pct=reserve,charge_kw=3.9,discharge_kw=3.9,
                ev_capacity_kwh=ev,ev_power_kw=6.19,ev_return_capacity_kwh=14)


class PlanningTests(unittest.TestCase):
    def test_four_sunny_days_have_advance_actions_and_capacity_is_not_reused(self):
        p=build_plan(**scenario())
        self.assertEqual(p['status'],'ready')
        self.assertEqual(len(p['days']),4)
        self.assertTrue(all(r['baseline_export_kwh']>0 for r in p['days']))
        self.assertAlmostEqual(sum(r['ev_charge_kwh'] for r in p['days']),1.68)
        self.assertGreater(p['days'][0]['battery_discharge_before_sunrise_kwh'],0)
        self.assertTrue(p['days'][0]['ev_window_start'])
        for a,b in zip(p['ledger'],p['ledger'][1:]):
            self.assertAlmostEqual(a['stored_kwh'],b['start_stored_kwh'])

    def test_energy_balance_and_reserve_for_every_interval(self):
        p=build_plan(**scenario())
        for r in p['ledger']:
            self.assertAlmostEqual(r['solar_kwh']+r['grid_kwh']+r['discharge_ac_kwh'],
                r['house_kwh']+r['ev_kwh']+r['charge_ac_kwh']+r['export_kwh'])
            self.assertAlmostEqual(r['stored_kwh']-r['start_stored_kwh'],
                r['charge_ac_kwh']*.9-r['discharge_ac_kwh']/.9)
            self.assertGreaterEqual(r['stored_kwh'],55.296*.4-1e-8)
            self.assertLessEqual(r['stored_kwh'],55.296+1e-8)

    def test_long_low_solar_conserves_but_one_cloudy_day_does_not(self):
        p=build_plan(**scenario(solar_kw=.1,load_kw=2))
        self.assertEqual(p['status'],'conserve')
        self.assertTrue(all(r['discharge_ac_kwh']==0 for r in p['ledger']))
        args=scenario()
        first=args['windows'][0].day
        args['points']=[IntervalPoint(p.at,p.watts*.01 if p.at.date()==first else p.watts) for p in args['points']]
        self.assertEqual(build_plan(**args)['status'],'ready')

    def test_conservation_ends_when_sunny_days_return(self):
        args=scenario(solar_kw=10,load_kw=2)
        second=args['windows'][1].day
        args['points']=[IntervalPoint(p.at,p.watts*.01 if p.at.date()<=second else p.watts) for p in args['points']]
        p=build_plan(**args)
        self.assertEqual(p['days'][0]['status'],'conserve')
        self.assertEqual(p['days'][1]['status'],'conserve')
        self.assertNotEqual(p['days'][2]['status'],'conserve')
        self.assertGreater(p['days'][2]['battery_discharge_kwh'],0)

    def test_after_sunset_next_sunset_is_tomorrow(self):
        p=build_plan(**scenario(now_hour=20))
        d={};publish_plan(d,p)
        self.assertEqual(d['next_sunset_date'],'2026-10-08')
        self.assertEqual(d['next_sunset_soc'],p['days'][1]['sunset_soc_pct'])

    def test_current_ev_capacity_zero_does_not_hide_later_surplus(self):
        p=build_plan(**scenario(ev=0))
        self.assertEqual(p['ev_capacity_allocated_kwh'],0)
        self.assertTrue(all(r['ev_return_opportunity_kwh']>0 for r in p['days']))

    def test_low_solar_share_still_schedules_useful_ev_energy(self):
        p=build_plan(**scenario(solar_kw=3,load_kw=1,soc=100,reserve=90,ev=10))
        self.assertGreater(p['ev_capacity_allocated_kwh'],0)
        self.assertLess(sum(r['ev_direct_solar_kwh'] for r in p['days'])/p['ev_capacity_allocated_kwh'],.9)
        self.assertLess(sum(r['export_kwh'] for r in p['ledger']),sum(r['export_kwh'] for r in p['baseline_ledger']))

    def test_revisions_recompute_without_waiting_for_confirmation(self):
        a=build_plan(**scenario(solar_kw=8));b=build_plan(**scenario(solar_kw=11))
        self.assertEqual(a['status'],b['status'])
        self.assertGreater(sum(r['baseline_export_kwh'] for r in b['days']),sum(r['baseline_export_kwh'] for r in a['days']))

    def test_missing_future_solar_is_not_interpreted_as_low_solar(self):
        args=scenario();args['points']=args['points'][:24]
        with self.assertRaisesRegex(ValueError,'cover daylight'):build_plan(**args)

    def test_storm_keeps_forecast_but_no_discretionary_charge(self):
        p=build_plan(**scenario(),storm=True)
        self.assertEqual(p['status'],'storm')
        self.assertEqual(p['ev_capacity_allocated_kwh'],0)
        self.assertEqual(len(p['days']),4)

    def test_published_surfaces_agree_and_do_not_inherit_unstable_hold(self):
        p=build_plan(**scenario());d={'forecast_reliability_status':'unstable','rolling_ev_status':'hold'}
        publish_plan(d,p)
        self.assertEqual(d['forecast_reliability_status'],'ready')
        self.assertEqual(d['rolling_ev_status'],'planned')
        self.assertEqual(d['next_sunset_expected_export'],d['today_predicted_export'])
        self.assertEqual(d['next_sunset_soc'],d['projected_sunset_soc'])
        self.assertEqual(d['rolling_dynamic_load_days_count'],4)
        self.assertFalse(d['rolling_ev_auto_charge_eligible'])

    def test_missing_learning_still_produces_current_forecast(self):
        args=scenario()
        curve,members=selected_curve(points=args['points'],live_points=args['points'],now=args['now'],
                                     memory={},observation={},latitude=38.9,longitude=-77)
        args['points']=curve
        p=build_plan(**args)
        self.assertEqual(len(p['days']),4)
        self.assertTrue(any(r['model']=='provider' for r in members))
        self.assertTrue(all(r['model']=='provider' for r in members if datetime.fromisoformat(r['start'])>=args['now']+timedelta(hours=24)))

if __name__=='__main__':unittest.main()
