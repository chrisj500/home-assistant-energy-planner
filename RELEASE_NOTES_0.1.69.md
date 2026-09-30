# Energy Planner 0.1.69

## Add EV parameters to Battery Outlook

The Battery Outlook section now includes a compact Lexus EV card with state of
charge, energy to target, learned and current charge rates, learned full-charge
energy, and Toyota range readings when available. Range values are matched to
the configured EV SOC entity; unavailable range telemetry is shown as
`Unavailable`. The card remains advisory only.

### Validation

- EV card rendering tests for configured-vehicle range matching and missing range sensors
- Dashboard YAML validation
