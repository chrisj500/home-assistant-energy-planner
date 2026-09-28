# Energy Planner 0.1.58

## High-resolution HVAC temperature learning

The thermostat remains the control/setpoint authority, while healthy HomePod room sensors provide the fine-resolution temperature movement signal for HVAC learning.

- Build three physical-room temperatures from Living Room, Guest Bedroom, and the averaged Main Bedroom pair.
- Use the median of those three physical-room temperatures as the precision HVAC movement signal.
- Only use the HomePod precision signal when all configured HomePod temperature/humidity feeds are fresh and healthy; otherwise fall back automatically to the thermostat.
- Keep thermostat temperature as the source of truth for setpoint error, unmet demand, target reached, and ETA distance.
- Use HomePod movement for live recovery-rate estimation so the learner can see fractional cooling/heating progress while the thermostat still reports whole degrees.
- Use HomePod movement for newly completed recovery-cycle rates while preserving thermostat-referenced indoor/outdoor deltas for compatibility with existing historical cycle matching.
- Use HomePod precision for passive thermal-loss windows and general response-rate learning.
- Expose the active temperature signal, source, precision value, and thermostat value in HVAC diagnostics.
- Retain all v0.1.57 target-time countdown behavior and restart resilience.

Thermostat control remains disabled; this changes measurement quality and learning only.
