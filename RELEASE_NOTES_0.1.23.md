# Energy Planner v0.1.23

## Fix Home Assistant Energy battery entities

v0.1.22 calculated whole-bank battery power and cumulative charged/discharged
energy internally, but omitted the three corresponding descriptions from the
sensor platform catalog. As a result, Home Assistant could not create the
entities.

This release registers:

- **Battery Bank Power** (`sensor.energy_planner_battery_bank_power`)
- **Battery Energy Charged** (`sensor.energy_planner_battery_energy_charged`)
- **Battery Energy Discharged** (`sensor.energy_planner_battery_energy_discharged`)

The cumulative energy entities use `total_increasing` state class and kWh units
for Home Assistant's Energy dashboard. A catalog regression test now verifies
that all three coordinator outputs are exposed as entities.
