# v0.1.39 — HVAC telemetry continuity and sunset forecast range

- Treat a valid unchanged thermostat or outdoor-temperature state as available.
  A 15-minute report-age limit was intermittently rejecting all thermostat
  attributes, placing HVAC learning and discretionary advice on hold. Explicit
  `unknown` and `unavailable` states still fail closed. HomePod rooms retain
  their independent `fresh` and `last_received` checks.
- Expose raw thermostat state, target/current temperature, humidity, report
  timestamp and age, together with outdoor, weather and stale-reading states,
  in HVAC model diagnostics. Include the configured HVAC entity IDs and live
  states in the Home Assistant diagnostics download.
- Anchor today's sunset-range lower bound to current weighted SOC while the sun
  is up: the daylight battery simulation does not model discharge. Scale the
  historical sunset-error allowance by the fraction of daylight remaining, so
  the range narrows as live observations replace the unobserved forecast.
  Future-day ranges keep their original horizon-specific allowance. These
  scenario bounds are engineering estimates, not statistical confidence limits.
- Preserve all stored HVAC samples, recovery history, thermal observations,
  and forecast calibration records. No dashboard YAML change is required;
  Dashboard v19 remains current.
