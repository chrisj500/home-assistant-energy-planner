# v0.1.28 — HVAC observation, safety holds, and shadow forecasts

Enable **Read-only HVAC learning and safety holds** in Energy Planner options.
Entity defaults match the supplied installation; confirm that `sensor.hvac_power`
measures only the outdoor condenser so Circuit 4 is not counted twice.

- Reads thermostat mode, reported HVAC action, setpoint, indoor temperature and
  humidity; both electrical circuits; measured outdoor temperature; and hourly
  weather through `weather.get_forecasts`.
- Reads individual HomePod measurements with source timestamp/freshness checks.
  Living and guest bedrooms each have one vote; the two main-bedroom devices
  are averaged first. The thermostat remains the setpoint-response reference.
- Persists at most 30 days of five-minute observations. Learns separate electrical
  signatures and temperature slopes for idle, fan, cooling and gas heating.
  Heating estimates cover electricity only, not gas consumption.
- Missing-load checks require an unmet setpoint and ten continuous minutes;
  inadequate temperature response requires thirty minutes. These are suspected
  anomalies, not equipment diagnoses. Gaps, restarts and setpoint changes reset
  observation windows. Catch-up/fault observations do not train normal duty.
- Missing/stale inputs or suspected faults close discretionary EV/headroom advice
  through the existing fail-closed policy. Original SOC reserve protections remain.
- HVAC Model Status exposes signatures, room health, mapped entities and hourly
  shadow estimates. Matching requires 36 observations over three days, outdoor
  temperature within 2 C, outdoor humidity within 15 points and target within 1 C.
  Unsupported conditions stay unknown. Bounds are empirical power envelopes,
  not calibrated SOC probability intervals.

## Intentional validation boundary

This release collects evidence and forecasts HVAC in **shadow mode**. It does not
yet replace the load used by the battery simulations: doing so now would double
count HVAC in the existing smoothed whole-house baseline, and there is no supplied
time-aligned training history to validate a replacement. No historical backfill,
holdout accuracy scoring, or thermal-control optimization is claimed. The next
promotion requires measured forecast errors and an aligned non-HVAC residual load.
Auto heat/cool target bands and future setpoint schedules are not modeled yet;
hourly predictions assume the current cool/heat mode and target persist.

Learning is opt-in and no thermostat, EV, battery or charger commands are issued
by the HVAC module. Tests use synthetic data; live HomeKit/thermostat operation
must be verified after installing and restarting Home Assistant.
