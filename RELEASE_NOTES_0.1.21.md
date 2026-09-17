# Energy Planner v0.1.21

## Live-anchored current-day solar curve

- Anchors today's corrected Forecast.Solar interval curve to the actual live Enphase production value at the current time.
- Blends that live correction back toward the provider interval shape over roughly one hour.
- Preserves the corrected remaining-energy total while changing only the timing/shape of today's forecast.
- Leaves all future forecast days untouched.

## Unified current-day outlook

- Uses the live-anchored day-0 curve for the rolling headroom model, EV/flexible-load advisory, four-day Battery Outlook, and next-sunset forecast.
- Reconciles today's dashboard sunset SOC, projected battery gain, grid import, and export with the same current-day model used by headroom decisions.
- Exposes the live-anchor source through existing forecast-source diagnostics.

## Why

Late-afternoon forecasts could remain internally consistent but understate near-term surplus when the paid interval curve declined faster than live Enphase production. This could show current SOC and sunset SOC as nearly identical even while the EcoFlow battery was actively charging from solar.

## Scope

- No changes to battery capacity, reserve policy, charge efficiency, future-day Forecast.Solar curves, or EcoFlow realtime controller behavior.
