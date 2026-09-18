# Energy Planner v0.1.24

## Allow existing installations to select battery power sensors

v0.1.22 introduced optional inputs for the three battery-stack power sensors,
but exposed them only during first-time configuration. Existing installations
could not set or correct those inputs from the integration's Options form.

This release adds all three battery power selectors to Options so an existing
Energy Planner entry can explicitly map its battery stacks. This is required
when the configured SOC entity names cannot be used to infer the corresponding
power entity IDs.

For a three-stack Smart Home Panel 2 installation, select:

- Battery power 1: `sensor.ecoflow_smart_home_panel_2_ac1_power`
- Battery power 2: `sensor.ecoflow_smart_home_panel_2_ac2_power`
- Battery power 3: `sensor.ecoflow_smart_home_panel_2_ac3_power`
