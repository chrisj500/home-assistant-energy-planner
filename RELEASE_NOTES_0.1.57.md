# Energy Planner 0.1.57

## Nest-style HVAC ETA countdown

- Convert HVAC recovery ETA from a repeatedly recalculated temperature-gap number into a target-time countdown.
- Anchor an expected target timestamp as soon as a heating/cooling demand is detected, even before the compressor/blower call begins.
- Decrement ETA on each one-minute coordinator update, so the display counts down even when the thermostat temperature is unchanged.
- Ignore short-term thermostat rounding/noise as a reason to reset the ETA. A transient 73°F -> 74°F report no longer doubles the displayed estimate.
- Once the current call has at least 10 minutes and enough measured temperature progress to derive a live recovery rate, materially different live evidence may re-anchor the expected target time.
- Limit live re-anchors to at most once every 5 minutes and require at least a 5-minute or 20% ETA difference.
- If the predicted target time expires while demand remains, re-anchor from the best current estimate rather than displaying a stuck zero.
- Persist the target timestamp across Home Assistant restarts while still refusing to bridge restart gaps for recovery-rate learning.
- Expose ETA target timestamp, raw instantaneous ETA, countdown method, last correction time, and correction reason in HVAC diagnostics.
- Make the thermostat dashboard prefer the backend countdown; keep learned-rate calculation only as a startup/fallback path.

Thermostat control remains disabled; this changes estimation/display behavior only.
