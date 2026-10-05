## Energy Planner 0.1.73

- Keep bounded export-defense headroom recommendations available during
  unstable forecast revisions when forecast freshness, storm safety, and
  horizon-matched accuracy evidence support them.
- Cap an unstable-forecast overnight discharge advisory at the modeled need,
  10% of battery capacity, and energy above the configured reserve.
- Shift calibration toward export avoidance by accounting for positive solar
  underprediction error and not assuming favorable overnight battery depletion.
- Forecast future EV solar windows while the vehicle is away; recheck presence
  and measured solar surplus before any immediate charging action.
