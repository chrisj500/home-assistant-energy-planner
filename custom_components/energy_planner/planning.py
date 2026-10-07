"""Four-day export-first planning with one energy-conserving battery ledger.

All energies are AC kWh except explicitly named stored battery quantities.
Plans are recommendations, never device commands.
"""
from __future__ import annotations
from datetime import timedelta
from math import isfinite

try:
    from .forecast_solar_shadow import power_at
except ImportError:
    from forecast_solar_shadow import power_at


def simulate(slots, *, initial, capacities, reserve, ceiling, charge_kw, discharge_kw,
             efficiency, discharge=True, ev=None, conserve_days=()):
    stored = list(initial)
    records = []
    ev = ev or {}
    for i, slot in enumerate(slots):
        dt = slot['hours']
        pv, house = slot['solar_kw'] * dt, slot['load_kw'] * dt
        vehicle = ev.get(i, 0.0)
        net = pv - house - vehicle
        start = sum(stored)
        charge = discharge_ac = export = imported = 0.0
        if net >= 0:
            for j, capacity in enumerate(capacities):
                accepted = min(net, charge_kw * dt, max(capacity * ceiling - stored[j], 0) / efficiency)
                stored[j] += accepted * efficiency
                net -= accepted
                charge += accepted
            export = net
        else:
            need = -net
            if discharge and slot['date'] not in conserve_days:
                for j, capacity in enumerate(capacities):
                    supplied = min(need, discharge_kw * dt, max(stored[j] - capacity * reserve, 0) * efficiency)
                    stored[j] -= supplied / efficiency
                    need -= supplied
                    discharge_ac += supplied
            imported = need
        records.append({**slot, 'solar_kwh': pv, 'house_kwh': house, 'ev_kwh': vehicle,
                        'start_stored_kwh': start, 'stored_kwh': sum(stored),
                        'charge_ac_kwh': charge, 'discharge_ac_kwh': discharge_ac,
                        'export_kwh': export, 'grid_kwh': imported})
    return records


def build_plan(*, now, windows, points, capacities, socs, load_by_day,
               reserve_pct, ceiling_pct=100, charge_kw=3.9, discharge_kw=3.9,
               efficiency=.9, ev_capacity_kwh=0, ev_power_kw=0, ev_return_capacity_kwh=0, storm=False):
    if not capacities or len(capacities) != len(socs) or any(not isfinite(v) or v <= 0 for v in capacities):
        raise ValueError('Battery capacities are unavailable')
    if any(not isfinite(v) or not 0 <= v <= 100 for v in socs):
        raise ValueError('Battery SOC is unavailable')
    if not 0 < efficiency <= 1 or not 0 <= reserve_pct <= ceiling_pct <= 100:
        raise ValueError('Battery reserve, ceiling or efficiency is invalid')
    if not points or any(not isfinite(p.watts) or p.watts < 0 for p in points):
        raise ValueError('Solar intervals are unavailable')
    capacities = list(capacities)
    capacity = sum(capacities)
    initial = [c * s / 100 for c, s in zip(capacities, socs)]
    slots, days = [], []
    for window in windows[:4]:
        day = window.day.isoformat()
        start = max(now, window.sunrise.replace(hour=0, minute=0, second=0, microsecond=0))
        end = window.sunrise.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        daylight_start = max(start, window.sunrise)
        if daylight_start < window.sunset:
            if points[0].at > daylight_start or points[-1].at < window.sunset:
                raise ValueError(f'Solar intervals do not cover daylight on {day}')
            if any((b.at-a.at).total_seconds() > 7200 and a.at < window.sunset
                   and b.at > daylight_start for a,b in zip(points, points[1:])):
                raise ValueError(f'Solar interval gap on {day}')
        load = load_by_day.get(day)
        if load is None or not isfinite(load) or load < 0:
            raise ValueError(f'Household load is unavailable for {day}')
        days.append((day, window))
        at = start
        while at < end:
            stop = min(at + timedelta(minutes=15), end)
            midpoint = at + (stop-at)/2
            watts = power_at(points, midpoint) if window.sunrise <= midpoint < window.sunset else 0
            slots.append({'at':at, 'end':stop, 'date':day, 'hours':(stop-at).total_seconds()/3600,
                          'solar_kw':max(watts,0)/1000, 'load_kw':load,
                          'daylight':window.sunrise <= midpoint < window.sunset})
            at = stop
    common = dict(initial=initial, capacities=capacities, reserve=reserve_pct/100,
                  ceiling=ceiling_pct/100, charge_kw=max(charge_kw,0),
                  discharge_kw=max(discharge_kw,0), efficiency=efficiency)
    # Conservation requires two complete low-solar days AND reserve pressure
    # even with 30% more solar. Ordinary revisions never enter this decision.
    optimistic_slots = [{**s, 'solar_kw':s['solar_kw']*1.3} for s in slots]
    optimistic = simulate(optimistic_slots, **common)
    low_days = []
    for day, window in days:
        group = [r for r in optimistic if r['date']==day]
        complete = now <= window.sunrise
        low_days.append(complete and sum(r['solar_kwh'] for r in group) < sum(r['house_kwh'] for r in group))
    sustained = len(low_days)>=2 and low_days[0] and low_days[1]
    first_two = [r for r in optimistic if r['date'] in {d[0] for d in days[:2]}]
    reserve_pressure = any(r['stored_kwh'] <= capacity*reserve_pct/100 + .05 for r in first_two)
    conserve = bool(sustained and reserve_pressure)
    protected = []
    if conserve:
        for (day,_),low in zip(days,low_days):
            if not low: break
            protected.append(day)
    common['conserve_days'] = protected
    discharge = not storm
    baseline = simulate(slots, **common, discharge=discharge)
    # Use each kWh of known EV capacity only once. Later-day possibilities are
    # reported separately and never silently assume that the car was driven.
    ev_remaining = max(ev_capacity_kwh,0)
    ev_schedule, rows = {}, []
    for day, window in days:
        indices = [i for i,s in enumerate(slots) if s['date']==day]
        before = simulate(slots, **common, discharge=discharge, ev=ev_schedule)
        export = sum(before[i]['export_kwh'] for i in indices)
        target = min(ev_remaining, export) if not storm else 0
        candidates = [i for i in indices if slots[i]['daylight']
                      and slots[i]['solar_kw'] > slots[i]['load_kw']]
        best = None
        # Contiguous charging, full charger power plus a partial final interval.
        for first in candidates:
            if target <= .001 or ev_power_kw <= 0:
                break
            remaining, proposal = target, {}
            for i in indices:
                if i < first or not slots[i]['daylight']:
                    continue
                energy = min(remaining, ev_power_kw*slots[i]['hours'])
                if energy <= 0: break
                proposal[i] = energy
                remaining -= energy
            trial = simulate(slots, **common, discharge=discharge, ev={**ev_schedule,**proposal})
            avoided = sum(before[i]['export_kwh']-trial[i]['export_kwh'] for i in indices)
            if avoided > .001 and (best is None or avoided > best[0]+1e-9):
                best = (avoided, proposal)
        selected = best[1] if best else {}
        ev_schedule.update(selected)
        allocated = sum(selected.values())
        ev_remaining -= allocated
        after = simulate(slots, **common, discharge=discharge, ev=ev_schedule)
        group = [after[i] for i in indices]
        daylight = [r for r in group if r['daylight']]
        before_sun = [r for r in group if r['at'] < window.sunrise]
        sunset = daylight[-1]['stored_kwh'] if daylight else group[0]['start_stored_kwh']
        sunrise = daylight[0]['start_stored_kwh'] if daylight else group[0]['start_stored_kwh']
        residual = sum(r['export_kwh'] for r in group)
        surplus_slots = [r for r in group if r['export_kwh'] > .001]
        first = min(selected) if selected else None
        last = max(selected) if selected else None
        rows.append({'date':day, 'sunset_at':window.sunset.isoformat(), 'start_at':group[0]['at'].isoformat(), 'solar_kwh':sum(r['solar_kwh'] for r in group),
                     'house_kwh':sum(r['house_kwh'] for r in group),
                     'sunrise_soc_pct':100*sunrise/capacity, 'sunset_soc_pct':100*sunset/capacity,
                     'baseline_export_kwh':export, 'ev_charge_kwh':allocated,
                     'ev_direct_solar_kwh':sum(min(r['ev_kwh'],max(r['solar_kwh']-r['house_kwh'],0)) for r in group),
                     'ev_battery_or_grid_kwh':sum(max(r['ev_kwh']-max(r['solar_kwh']-r['house_kwh'],0),0) for r in group),
                     'ev_window_start':slots[first]['at'].isoformat() if first is not None else None,
                     'ev_window_end':(slots[last]['at']+timedelta(hours=selected[last]/ev_power_kw)).isoformat() if last is not None else None,
                     'residual_export_kwh':residual, 'other_load_opportunity_kwh':residual,
                     'flex_window_start':surplus_slots[0]['at'].isoformat() if surplus_slots else None,
                     'flex_window_end':surplus_slots[-1]['end'].isoformat() if surplus_slots else None,
                     'ev_return_opportunity_kwh':min(residual, max(ev_return_capacity_kwh,ev_capacity_kwh), ev_power_kw*sum(r['hours'] for r in surplus_slots)) if ev_power_kw>0 else 0,
                     'grid_import_kwh':sum(r['grid_kwh'] for r in group),
                     'battery_discharge_before_sunrise_kwh':sum(r['discharge_ac_kwh']/efficiency for r in before_sun),
                     'battery_charge_kwh':sum(r['charge_ac_kwh']*efficiency for r in group),
                     'battery_discharge_kwh':sum(r['discharge_ac_kwh']/efficiency for r in group),
                     'status':'storm' if storm else 'conserve' if day in protected else 'use_surplus' if export>.05 else 'self_consumption'})
    final = simulate(slots, **common, discharge=discharge, ev=ev_schedule)
    return {'model':'export_first_four_day_v1', 'status':'storm' if storm else 'conserve' if conserve else 'ready',
            'reason':'Storm protection is active.' if storm else 'Two low-solar days reach reserve even with 30% more solar.' if conserve else 'Use batteries for household demand and schedule loads to absorb forecast surplus.',
            'days':rows, 'ledger':final, 'baseline_ledger':baseline,
            'ev_capacity_allocated_kwh':sum(ev_schedule.values()),
            'next_sunset_date':next((day for day,w in days if w.sunset>now),days[-1][0]),
            'efficiency':efficiency,
            'reserve_pct':reserve_pct, 'capacity_kwh':capacity}


def publish_plan(data, plan):
    """Replace operational display quantities together; retain observed counters."""
    days = plan['days']
    risk = [r for r in days if r['baseline_export_kwh'] > .05]
    first_risk = risk[0] if risk else None
    today = days[0]
    next_day = days[1] if len(days)>1 else today
    next_sunset = next(r for r in days if r['date']==plan['next_sunset_date'])
    ev = next((r for r in days if r['ev_charge_kwh']>.001), None)
    public = {k:v for k,v in plan.items() if k not in ('ledger','baseline_ledger')}
    data.update(four_day_plan_status=plan['status'], four_day_plan=public,
        forecast_reliability_status=plan['status'], forecast_reliability_reason=plan['reason'],
        battery_outlook_status='ready', battery_outlook_reason=plan['reason'],
        rolling_ev_auto_charge_eligible=False,
        rolling_ev_status='planned' if ev else 'no_window',
        rolling_ev_status_reason='Follow the four-day load plan.' if ev else 'No EV charge allocated from current available capacity.',
        rolling_ev_auto_charge_reason='Planned load; immediate charging requires separate live verification.',
        rolling_ev_recommended_energy_kwh=ev['ev_charge_kwh'] if ev else 0,
        rolling_ev_window_start=ev['ev_window_start'] if ev else None,
        rolling_ev_window_end=ev['ev_window_end'] if ev else None,
        rolling_ev_window_solar_kwh=ev['ev_direct_solar_kwh'] if ev else 0,
        rolling_ev_window_grid_kwh=None,
        rolling_ev_window_solar_fraction_pct=100*ev['ev_direct_solar_kwh']/ev['ev_charge_kwh'] if ev else None,
        rolling_ev_headroom_preserved_kwh=ev['baseline_export_kwh']-ev['residual_export_kwh'] if ev else 0,
        rolling_ev_model=plan['model'], forecast_export_model=plan['model'],
        rolling_dynamic_load_forecast_model=plan['model'], rolling_dynamic_load_forecast_status=plan['status'],
        forecast_export_risk=bool(risk), forecast_export_risk_date=first_risk['date'] if risk else 'none',
        forecast_export_risk_days=[r['date'] for r in risk],
        forecast_export_wall_energy_kwh=first_risk['baseline_export_kwh'] if risk else 0,
        forecast_export_headroom_kwh=min(first_risk['baseline_export_kwh']*plan['efficiency'], plan['capacity_kwh']*(1-plan['reserve_pct']/100)) if risk else 0,
        forecast_export_risk_reason='forecast_surplus' if risk else 'clear',
        rolling_dynamic_load_days_count=len(risk), rolling_dynamic_load_risk_dates=[r['date'] for r in risk],
        rolling_dynamic_load_total_kwh=sum(r['baseline_export_kwh'] for r in days),
        rolling_dynamic_load_next_3d_kwh=sum(r['baseline_export_kwh'] for r in days[:3]),
        rolling_ev_surplus_next_3d_kwh=sum(r['baseline_export_kwh'] for r in days[:3]),
        rolling_ev_surplus_horizon_kwh=sum(r['baseline_export_kwh'] for r in days),
        authoritative_headroom_risk=today['baseline_export_kwh']>.05,
        authoritative_headroom_status='risk_today' if today['baseline_export_kwh']>.05 else 'risk_future' if risk else 'clear',
        authoritative_headroom_risk_date=first_risk['date'] if risk else 'none',
        authoritative_headroom_capacity_export_kwh=today['residual_export_kwh'],
        authoritative_headroom_action=plan['reason'],
        headroom_release=today['battery_discharge_before_sunrise_kwh']>.05 or next_day['battery_discharge_before_sunrise_kwh']>.05,
        today_recommended_presolar_discharge=today['battery_discharge_before_sunrise_kwh'],
        recommended_overnight_discharge=next_day['battery_discharge_before_sunrise_kwh'],
        today_strategy=today['status'], tomorrow_strategy=next_day['status'],
        today_strategy_reason=plan['reason'], tomorrow_strategy_reason=plan['reason'],
        today_plan_ready=True, tomorrow_plan_ready=len(days)>1,
        projected_sunset_soc=next_sunset['sunset_soc_pct'], next_sunset_soc=next_sunset['sunset_soc_pct'],
        next_sunset_date=next_sunset['date'], next_sunset_expected_export=next_sunset['residual_export_kwh'],
        next_sunset_expected_grid_import=next_sunset['grid_import_kwh'], next_sunset_expected_charge=next_sunset['battery_charge_kwh'],
        projected_charge_to_sunset=today['battery_charge_kwh'],
        projection_model=plan['model'], next_sunset_forecast_source=plan['model'])
    for row, prefix in ((today,'today'),(next_day,'tomorrow')):
        data[prefix+'_projected_sunset_soc'] = row['sunset_soc_pct']
        data[prefix+'_predicted_export'] = row['residual_export_kwh']
        data[prefix+'_predicted_grid_import'] = row['grid_import_kwh']
        data[prefix+'_baseline_export'] = row['baseline_export_kwh']
        data[prefix+'_projection_model'] = plan['model']
        data[prefix+'_discretionary_energy'] = row['baseline_export_kwh']
    data['forecast_solar_shadow_projected_sunset_soc'] = next_sunset['sunset_soc_pct']
    data['forecast_solar_shadow_export_today'] = today['residual_export_kwh']
    data['counterfactual_projected_sunset_soc_pct'] = today['sunset_soc_pct']
    data['forecast_solar_shadow_model'] = plan['model']
    data['projected_sunset_soc_tomorrow'] = next_day['sunset_soc_pct']
    data['predicted_export_tomorrow'] = next_day['residual_export_kwh']
    data['baseline_export_tomorrow'] = next_day['baseline_export_kwh']
    data['counterfactual_projected_export_kwh'] = today['baseline_export_kwh']
    # Compatibility rows all describe the same plan, never stress totals.
    data['rolling_day_plans'] = [{**r, 'display_sunset_soc_pct':r['sunset_soc_pct'],
        'start_soc_pct':r['sunrise_soc_pct'], 'display_confidence':'planning',
        'confidence':'planning', 'export_kwh':r['residual_export_kwh'],
        'capacity_export_kwh':r['residual_export_kwh'],
        'dynamic_load_needed':r['baseline_export_kwh']>.05,
        'dynamic_load_needed_kwh':r['baseline_export_kwh'],
        'export_defense_risk':r['baseline_export_kwh']>.05,
        'export_defense_wall_energy_kwh':r['baseline_export_kwh']}
        for r in days]
