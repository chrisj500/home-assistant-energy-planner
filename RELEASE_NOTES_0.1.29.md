# v0.1.29 — HVAC dashboard overview

- Dashboard v12 adds a read-only thermostat dial, reported operating-state badge, target/current temperature, humidity, four room temperatures, combined electrical power and today's electricity.
- Cooling, heating, fan-only, idle, off, drying and defrosting have distinct indicators. The state is explicitly thermostat-reported, not proof of equipment operation.
- HVAC electricity tracking runs independently of opt-in shadow learning. It integrates fresh, nonnegative condenser and blower power in W; gas fuel is excluded.
- Daily totals persist across restarts and reset at Home Assistant local midnight. Gaps over five minutes, missing readings and restart gaps are excluded. Coverage and partial-day labels prevent presenting missing history as zero usage. No historical backfill.
- Requires two distinct circuit entities; ensure the condenser sensor does not already include blower power.

## Install

Update the integration to 0.1.29 and restart Home Assistant. Separately replace the dashboard raw configuration with `dashboards/energy-planning.yaml` (custom:button-card required). HACS does not automatically replace a manually installed dashboard.

The example uses `climate.thermostat`, the supplied HomePod entity IDs, and default Energy Planner sensor names. Adjust those references if renamed. Tap the dial to open thermostat details; the dial itself does not issue HVAC commands.
