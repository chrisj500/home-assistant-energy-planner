# Energy Planner v0.1.22

## Home Assistant Energy dashboard battery flow

- Adds a whole-bank **Battery Bank Power** sensor by summing the three battery-stack power sensors.
- Adds cumulative **Battery Energy Charged** and **Battery Energy Discharged** sensors in kWh with total-increasing statistics for Home Assistant's Energy dashboard.
- Positive source power is treated as charging and negative source power as discharging, matching the EcoFlow stack-power convention used by the dashboard.
- Uses one-minute trapezoidal integration with explicit zero-crossing handling.
- Skips long telemetry gaps rather than inventing missing energy.
- Persists cumulative charge/discharge totals across Home Assistant restarts.
- Automatically derives stack power entities from configured SOC entities ending in `_battery` or `_battery_level`. Optional stack-power inputs are available in Energy Planner options as overrides.

## Energy dashboard configuration

Configure one battery system for the whole bank:

- Energy discharged from the battery: **Battery Energy Discharged**
- Energy charged into the battery: **Battery Energy Charged**
- Display name: **EcoFlow Battery Bank**
- Type of power measurement: **Inverted**
- Power sensor: **Battery Bank Power**
- Battery state of charge sensor: **Whole Bank SOC**
- Battery capacity: **49.152 kWh**
