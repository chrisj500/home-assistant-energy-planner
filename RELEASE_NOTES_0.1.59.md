# Energy Planner 0.1.59

## Restart-resilient HVAC learning

Home Assistant restarts are now treated as normal runtime events rather than automatic HVAC-cycle interruptions.

- Persist and restore active recovery calls, ETA target timestamps, general response windows, passive thermal windows, and the previous HVAC observation.
- On the first live observation after restart, validate the restored mode, action, and target before resuming.
- Brief gaps up to 10 minutes retain full HVAC logical and measurement continuity, so a typical 1-minute HA restart does not reset call duration, ETA countdown, or recovery-rate learning.
- Repeated brief restarts preserve the same recovery call and target timestamp.
- Longer restart gaps may still retain the logical recovery call and ETA when mode/action/target agree, while rebasing the temperature-rate measurement segment so unobserved time is never invented as measured thermal response.
- If critical HVAC inputs are still unavailable during startup, defer restart validation and retain the persisted call until those inputs return.
- Preserve the HVAC energy integrator's previous measured-power sample across restart. Its existing 5-minute adjacency guard determines whether a short gap is integrated; longer gaps remain excluded.
- HomePod room telemetry is now a precision enhancement rather than a hard HVAC dependency. If it is stale or unavailable, the learner falls back to thermostat temperature instead of entering HVAC hold.
- When the temperature signal changes between HomePod precision and thermostat fallback, preserve the logical recovery call but rebase the rate-learning segment so unlike sensor sources are never compared directly.
- Add restart diagnostics for resume status, restart gap, restored call/window state, and whether rate learning was rebased.

No thermostat control is enabled. This release changes persistence, learning continuity, and diagnostics only.
