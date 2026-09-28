# Energy Planner 0.1.54

## HVAC restart resilience

Home Assistant restarts are treated as a normal operating condition rather than an exceptional event.

- Persist the last successful hourly HVAC weather forecast with the HVAC learner state.
- Never clear the last-known-good weather forecast before a refresh succeeds.
- After a transient weather service failure, keep using the persisted forecast for up to 6 hours and retry after 5 minutes instead of caching an empty result for 30 minutes.
- Expire stale cached weather after 6 hours so a prolonged outage remains visible rather than silently masking it.
- Save a successful or failed weather refresh immediately so a second restart cannot erase the cache/audit state before the main update cycle completes.
- Add weather diagnostics for source, last success, last attempt, last error, cache age, cache size, retry interval, and maximum fallback age.
- Record when a restart interrupted an in-progress HVAC recovery cycle or passive thermal window while preserving all completed learning history.
- Add thermal-window rejection diagnostics, including duration, insufficient temperature movement, small indoor/outdoor delta, wrong drift direction, and coefficient bounds.
- Add regression tests for restart weather fallback, stale-cache expiry, restart interruption accounting, and thermal rejection reasons.

Operational behavior remains conservative: HVAC hourly forecasts are still shadow-only and thermostat control remains disabled.
