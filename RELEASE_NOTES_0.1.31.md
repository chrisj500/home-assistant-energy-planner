# v0.1.31 — HVAC room layout and thermostat rollback

- Reverts the HVAC thermostat visual to the cleaner v0.1.29 circular ring while
  retaining the v0.1.30 electricity and learning diagnostics.
- Fixes the thermostat status header when a climate entity exposes its HVAC
  mode but no `hvac_action`: for example, `cool` now displays **Cool mode**
  instead of **State unavailable**.
- Reorganizes the four indoor-climate tiles into a physical-room layout:
  1. **Main bedroom** — average of the Left and Right HomePods, including
     temperature and humidity.
  2. **Guest bedroom**.
  3. **Living room**.
  4. **House average** — equal-weight average of Living Room, Guest Bedroom,
     and the already-averaged Main Bedroom, so the two Main Bedroom HomePods do
     not double-weight that room.
- Each room tile now shows temperature and relative humidity. Main Bedroom and
  House Average also show how many expected inputs are available.
- Dashboard room lookup accepts both current
  `sensor.homepod_indoor_climate_...` IDs and persisted
  `sensor.home_homepod_indoor_climate_...` IDs, with a suffix fallback for
  entity-registry variations.
- HVAC learning uses the same bidirectional entity-resolution logic, so
  enabling the learner does not require renaming existing Home Assistant
  entities.
- Stops force-normalizing saved room prefixes in Options.
- Removes the global storm state from each individual Battery Outlook day
  header; storm protection remains clearly shown in Forecast Guard / What To Do
  instead of looking like a four-day weather forecast.

## Dashboard update

HACS updates the integration code but cannot replace a manually maintained
Lovelace dashboard. After updating to v0.1.31 and restarting Home Assistant,
replace the dashboard raw configuration with
`dashboards/energy-planning.yaml` from this release (dashboard v14).
