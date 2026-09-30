# Energy Planner 0.1.68

## Correct Lexus SOC freshness and battery flow display

Lexus SOC freshness now follows Home Assistant's latest entity report. A fresh
unchanged SOC no longer appears stale just because its numeric value has not
changed since the last update. This allows the live solar capture advisory to
use the available Lexus capacity already reported by the Toyota integration.

The Battery Bank card aligns its title to the left and reserves room for the
charge/discharge state indicator. Discharge power is labeled explicitly as
`DISCHARGE RATE` while continuing to show power delivered to the home.

### Validation

- SOC report-heartbeat and fallback freshness tests
- Lovelace battery card discharge rendering test
- Dashboard YAML validation
