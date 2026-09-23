# v0.1.33 — HVAC learner support for thermostats without hvac_action

Fixes HVAC learning remaining `unavailable` when a climate entity exposes its
HVAC mode but does not publish the optional `hvac_action` attribute.

## Behavior

- Explicit thermostat `hvac_action` remains authoritative when present.
- When it is absent, Energy Planner now infers only actions supported by both
  the HVAC mode and measured circuit power:
  - `cool` + condenser load >= 100 W → `cooling`
  - `cool`/dry + blower load >= 20 W but condenser below 100 W → `fan`
  - `cool`/dry with neither active load → `idle`
  - `heat` + blower/control load >= 20 W → `heating`
  - `heat` with low blower/control load → `idle`
  - `fan_only` + blower load >= 20 W → `fan`
  - `off` → `off`
- Ambiguous `heat_cool` states remain fail-closed unless active condenser load
  proves cooling. The planner does not guess gas heating from mode alone.
- HVAC diagnostics now expose:
  - `thermostat_mode`
  - `thermostat_action_reported`
  - `thermostat_action_effective`
  - `thermostat_action_source`
- Unavailable learner diagnostics now name the missing input(s) instead of
  returning only a generic failure message.

This keeps the learner read-only and conservative while allowing thermostats
that omit `hvac_action` to collect valid baseline samples.
