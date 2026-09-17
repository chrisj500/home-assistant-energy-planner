# Energy Planner v0.1.20

## Midnight rollover protection

- Ignores transient near-zero `solar_remaining` values before sunrise when the paid Forecast.Solar interval curve still contains material solar energy for the new day.
- Keeps the raw current-day interval curve authoritative during that rollover window instead of collapsing the forecast to the minimum scale factor.
- Resumes normal locally corrected current-day scaling once the remaining-energy sensor is populated or daylight begins.

## EV flexible-load guidance

- Keeps a near-term headroom-risk EV outlook strategically green even when the EV is already at its target.
- Auto-charge remains ineligible while the EV has no energy-to-target, but the reason now explicitly recommends creating EV charging headroom through normal driving or errands before the preferred solar window.
- No new entities, counters, or control variables were added.

## Scope

- No changes to the 7-day headroom model, battery capacity, reserve policy, charge efficiency, dynamic-load calculation, or EcoFlow realtime controller behavior.
