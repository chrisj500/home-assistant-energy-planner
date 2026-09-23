# v0.1.34 — Outdoor delta, HVAC recovery ETA, and passive thermal model

## Dashboard

- Dashboard v16 shows the configured current outdoor temperature directly under
  the thermostat, together with a signed delta versus the active thermostat
  setpoint.
- The HVAC learner panel now surfaces recovery and passive thermal-model status
  rather than hiding those learned metrics in diagnostics.

## Recovery-time learning

Energy Planner now learns empirical heating/cooling recovery behavior from
actual HVAC calls.

- Explicit/inferred HVAC action from v0.1.33 is used to identify active calls.
- After an active call has run for at least 10 minutes and produced at least
  0.15 °C of directional progress, the planner can publish a **provisional live
  recovery ETA** from the current call.
- Completed calls lasting 10 minutes to 4 hours are retained for 30 days.
- Once at least two relevant completed calls exist, historical median recovery
  rate can be used when a current call is too young to establish a live rate.
- Historical calls are matched to the current indoor/outdoor temperature
  difference when possible.
- Recovery estimates are capped at 360 minutes and are explicitly labeled
  provisional/live or historical.

## Passive thermal model

The planner also estimates effective passive temperature drift while HVAC is
idle/off and both measured HVAC circuits are below their active thresholds.

- A passive window must last at least 30 minutes.
- The indoor temperature must move at least 0.15 °C toward outdoor temperature.
- Indoor/outdoor separation must be at least 2 °C.
- The planner estimates a first-order thermal decay coefficient (1/hour), an
  effective time constant, and expected passive °C/hour drift at the current
  indoor/outdoor temperature difference.
- A valid live idle window is labeled **provisional**.
- Three completed valid passive windows are required before the historical
  thermal model is labeled **ready**.

This is an empirical envelope/thermal-mass model, **not a BTU/hr heat-loss
calculation**. True heat loss in BTU/hr would require an estimate of the
building's effective thermal capacitance or a calibrated heating/cooling output
in thermal units.

## Diagnostics

The existing HVAC Model Status attributes now include nested `recovery` and
`thermal` diagnostics, including estimate source, sample/cycle counts, live
call/window age, recovery rate/ETA, thermal coefficient, thermal time constant,
and predicted passive drift.
