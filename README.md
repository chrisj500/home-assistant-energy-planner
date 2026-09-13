# Home Assistant Energy Planner

Forecast-aware battery headroom and flexible-load planning for Home Assistant.

## v0.1.6 scope

The current release keeps the planner as the slow, advisory planning layer rather than replacing fast device controllers.

It provides:

- Weighted whole-bank SOC from three battery SOC entities with configurable weights.
- Stored battery energy and available battery headroom.
- Policy-aware projected sunset SOC that never assumes protected-daylight battery discharge.
- A next-solar-period simulation using tomorrow's solar forecast, daylight window, base house load, battery headroom, charge efficiency, storm state, and optional EV availability.
- Evening strategy recommendations: `HOLD`, `USE_DISCRETIONARY_LOADS`, `CREATE_HEADROOM`, `PRESERVE_FOR_RESILIENCE`, or `INSUFFICIENT_DATA`.
- Required headroom, headroom margin/shortfall, projected maximum SOC tomorrow, projected export/import, discretionary energy, and recommended overnight discharge sensors.
- EV guidance that prefers using otherwise-exported solar before deliberately cycling the stationary battery.
- UI configuration and options; no YAML is required for the integration itself.

### Planning priorities

The strategy engine is intentionally ordered around these goals:

1. Minimize exported solar.
2. Minimize grid purchases over the long term, not merely shift purchases between days.
3. Preserve stored solar unless discharging creates useful headroom.
4. Respect storm protection and the configured minimum backup reserve.
5. Prefer discretionary loads such as EV charging before creating stationary-battery headroom when practical.

The configured minimum reserve is a floor, not a routine overnight target.

### Advisory-only control model

v0.1.6 does **not** automatically change EcoFlow operating mode, discharge the battery, grid-charge the battery, or lower the backup reserve. It publishes planning intent and supporting metrics for validation first. The fast EcoFlow solar-surplus controller should remain responsible for real-time surplus capture.

## Installation with HACS

Until this repository is added to the default HACS store, add it as a custom repository:

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/chrisj500/home-assistant-energy-planner` with category **Integration**.
3. Download **Home Assistant Energy Planner**.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add Integration** and search for **Energy Planner**.

## Configuration

The setup flow asks for the core battery and forecast entities, including:

- three battery SOC sensors
- battery weights, such as `3,2,3`
- total battery capacity in kWh
- charge-limit number entity
- backup-reserve number entity
- storm-warning binary sensor
- solar forecast for today and tomorrow
- optional sunset-projection inputs
- optional tomorrow solar peak-time sensor
- expected/base house load power sensor for tomorrow planning
- optional EV SOC and location entities

Options include:

- minimum reserve percentage
- EV target SOC
- solar-harvest preferred grid import
- harvest capture factor
- AC-to-battery charge efficiency
- minimum predicted export before recommending discretionary loads
- legacy strong-solar threshold retained for backward-compatible EV guidance

## Safety model

Priority is deliberately conservative:

1. Native device safety / storm protection
2. Minimum reserve protection
3. Preserve stored solar by default
4. Reduce future solar export
5. Flexible-load recommendations
6. Headroom creation only when modeled storage capacity is insufficient

If required tomorrow-planning inputs are unavailable, the strategy reports `INSUFFICIENT_DATA` rather than recommending discharge.

## Development

The integration remains separate from the fast EcoFlow solar-surplus controller. A future control release can consume the validated strategy intent and translate it into explicit EcoFlow operating modes such as hold/preserve, solar capture, controlled headroom creation, or deliberate grid charge.
