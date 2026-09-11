# Home Assistant Energy Planner

Forecast-aware battery headroom and flexible-load planning for Home Assistant.

## v0.1.0 scope

The first release intentionally focuses on the slow planning/control layer rather than replacing fast device controllers.

It provides:

- Weighted whole-bank SOC from three battery SOC entities with configurable weights.
- Stored battery energy and available battery headroom.
- Upcoming solar forecast from Forecast.Solar-compatible entities.
- Flat-rate solar-headroom control: when the next solar period is strong, an elevated backup reserve can be lowered back to the configured minimum to create room for solar.
- Storm-warning interlock: reserve control is suppressed while the configured storm-warning binary sensor is active or unavailable.
- EV SOC and a basic advisory EV charge plan.
- UI configuration and options; no YAML is required for the integration itself.

The integration never raises backup reserve and never grid-charges the stationary battery.

## Installation with HACS

Until this repository is added to the default HACS store, add it as a custom repository:

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/chrisj500/home-assistant-energy-planner` with category **Integration**.
3. Download **Home Assistant Energy Planner**.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add Integration** and search for **Energy Planner**.

## Configuration

The setup flow asks for:

- three battery SOC sensors
- battery weights, such as `3,2,3`
- total battery capacity in kWh
- charge-limit number entity
- backup-reserve number entity
- storm-warning binary sensor
- solar forecast for today and tomorrow
- optional EV SOC and location entities

Options provide:

- automatic solar headroom on/off
- minimum reserve percentage
- strong-solar threshold in kWh
- EV target SOC

## Safety model

Priority is deliberately conservative:

1. Native device safety / storm protection
2. Minimum reserve protection
3. Solar headroom optimization
4. Flexible-load recommendations

If required control inputs are unavailable, the integration does nothing.

## Development

`v0.1.0` is the first migration step from the existing Home Assistant YAML planner. The existing fast EcoFlow solar-surplus controller should remain in place during validation. Once values match the current dashboard, later releases can absorb additional forecast correction, daily accounting, HVAC/load prediction, and flexible-load actuation.
