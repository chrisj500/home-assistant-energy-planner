# v0.1.30 — HVAC dashboard, learning progress and safety diagnostics

- Dashboard v13 replaces the generic thermostat ring with an Aqara-inspired
  rounded black face: upper activity arc, large setpoint, measured temperature
  and humidity, plus/minus marks, and mode/fan/home indicators.
- Corrects the Guest Bedroom and Main Bedroom HomePod entity prefixes from the
  accidental `sensor.home_homepod_indoor_climate_...` form to the entities
  actually created by HomePod Indoor Climate. Existing saved v0.1.29 prefix
  options are repaired at runtime when the corrected entities exist and are
  normalized the next time Options is opened.
- **Electricity now** no longer rejects an unchanged numeric circuit state after
  five minutes. A stable `0 W` remains valid until Home Assistant marks the
  condenser or blower/control entity `unknown`/`unavailable`. Actual gaps
  still break daily-energy integration rather than being backfilled as zero.
- HVAC power entities expose per-source state, unit, last-reported timestamp,
  validity reason and duplicate-mapping diagnostics.
- General HVAC baseline learning is now explicit: ready after **36 clean
  five-minute samples across at least 3 days**. The dashboard shows sample/day
  progress. Hourly shadow forecasts remain separately unsupported until an hour
  has 36 weather-matched samples across at least 3 days.
- Forecast reliability exposes its action-learning requirement directly:
  **3 scored sunset forecasts plus 3 valid overnight calibration records**.
  The dashboard shows both counters and does not imply that "learning" is
  indefinite.
- Storm gating now distinguishes `storm_active` from
  `storm_sensor_unavailable`. The Forecast Guard shows the exact configured
  storm entity and raw Home Assistant state.
- Adds `Forecast Learning Progress` and `Storm Safety Status` entities and
  expands HVAC/forecast entity attributes for troubleshooting.

## Dashboard update

HACS updates the integration code, not a manually imported Lovelace dashboard.
After installing v0.1.30 and restarting Home Assistant, replace the dashboard
raw configuration with `dashboards/energy-planning.yaml` from this release.
