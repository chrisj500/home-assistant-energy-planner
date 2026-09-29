# Energy Planner 0.1.63

## Stabilize multi-day battery forecasts against short-term load swings

Future battery SOC no longer applies the same responsive 3-hour/24-hour load
blend to every day in the forecast horizon.

- Keep the current day responsive to recent household behavior using the existing
  45% recent-3h / 55% recent-24h blend.
- Decay the recent-3h contribution by 65% for each additional forecast day,
  rapidly anchoring future days to the more stable 24-hour household baseline.
- Use the same per-day load profile consistently in nominal, energy-security,
  export-defense, counterfactual, and EV/headroom simulations.
- Expose each day's planning load plus the original 3-hour and 24-hour inputs in
  diagnostics so forecast movement can be attributed directly to solar or load.
- Keep the overnight empirical battery-depletion calibration unchanged.

## Show the actual asymmetric battery scenario envelope

Battery Outlook no longer labels historical MAE as a symmetric `±` range.

- Keep the large sunset SOC number as the nominal/best estimate.
- Show the modeled low-to-high scenario envelope beneath the battery icon.
- Preserve confidence/learning state alongside the range.
- This makes high-side near-full outcomes visible without implying that the
  envelope is a probability interval.

## Remove obsolete manual topology keys from existing config entries

Config entry schema moves to version 2 and removes the deprecated
`capacity_kwh`, `soc_weights`, and `auto_battery_topology` keys from
existing entry data/options during migration.

- Other configuration is preserved unchanged.
- Live topology remains exclusively EcoFlow-discovered / last-known-good.
- Historical reliability-record migration can still interpret old records using
  the retained legacy compatibility constants.

This release closes the startup/config cleanup left after v0.1.62.
