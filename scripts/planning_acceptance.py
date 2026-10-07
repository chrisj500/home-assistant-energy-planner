"""Reproduce synthetic acceptance examples; no household diagnostic data needed."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_planning import scenario
from planning import build_plan

print('# Four-day planner acceptance results\n')
print('Synthetic fixtures, not observed household savings. Tests use a 55.296 kWh bank, 99% initial SOC, 40% reserve, and a 6.19 kW EV charger.\n')
for title,args in [('Four sunny days at 02:00',scenario()),
                   ('Mixed solar/grid EV window',scenario(solar_kw=3,load_kw=1,soc=100,reserve=90,ev=10)),
                   ('Sustained low solar',scenario(solar_kw=.1,load_kw=2))]:
    p=build_plan(**args)
    print(f'## {title}\n\nStatus: {p["status"]}.\n')
    print('| Date | Solar kWh | House kWh | Battery before sunrise kWh | EV kWh | Remaining export kWh |')
    print('|---|---:|---:|---:|---:|---:|')
    for r in p['days']:
        keys=['solar_kwh','house_kwh','battery_discharge_before_sunrise_kwh','ev_charge_kwh','residual_export_kwh']
        print('| '+r['date']+' | '+' | '.join(f'{r[k]:.2f}' for k in keys)+' |')
    baseline=sum(r['export_kwh'] for r in p['baseline_ledger'])
    after=sum(r['export_kwh'] for r in p['ledger'])
    print(f'\nExport before the allocated EV load: {baseline:.2f} kWh. After: {after:.2f} kWh.\n')
print('## Checks\n\nTests enforce AC energy balance, storage losses, reserve and capacity at every interval; continuous battery state across days; no reuse of EV capacity; planned windows before sunrise and while away; retention of mixed-supply opportunities; revision updates without confirmation waits; conservation ending when solar returns; partial provider coverage; and common dashboard/forecast outputs.')
