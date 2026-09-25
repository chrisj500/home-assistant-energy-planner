# Energy Planner 0.1.44

## Sunset uncertainty

- Same-day displayed uncertainty now contracts with the fraction of daylight still unobserved.
- Historical sunset MAE remains the full-horizon baseline for future days.
- Today's live-anchored display confidence is no longer downgraded solely because the multi-day provider forecast is unstable.

## Dashboard

- Moves the uncertainty/confidence line below the sunset battery fill icon.
- Uses Energy Planner's weighted whole-bank SOC as the Battery Bank headline, with the EcoFlow aggregate as a fallback, so the bank card and Battery Outlook use the same SOC definition.

## Battery power telemetry

- Rejects percentage/SOC entities accidentally configured as battery power sensors.
- Automatically falls back to the matching EcoFlow *_power entity when available.
- Prevents SOC percentages from being summed and interpreted as watts in battery-flow telemetry.
