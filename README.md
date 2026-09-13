# Home Assistant Energy Planner

Forecast-aware battery headroom and solar-export planning for Home Assistant.

## v0.1.8 scope

Energy Planner is the slow, advisory planning layer. The fast EcoFlow Solar Surplus integration remains responsible for real-time surplus capture.

v0.1.8 refactors the forecast around **physical AC energy flow** rather than a generic harvest percentage. The planner now mirrors the installed EcoFlow Solar Surplus controller's charging policy when that integration is available, including its effective minimum/maximum charge rate, rate step, channel thresholds, preferred import, export-start threshold, and ramp behavior.

It provides:

- Weighted whole-bank SOC from three battery SOC entities and capacity weights.
- Per-bank SOC simulation so a single full DPU can become ineligible without pretending the whole bank is full.
- Rolling **Today** and **Next Day** plans; Next Day starts from Today's projected per-bank sunset SOCs.
- Live remaining-day projection using corrected solar remaining, expected load to sunset, current production, and the fast-controller model.
- Full-day planning using the daily solar total, sunrise/sunset, forecast peak time, base load, and controller model.
- Physically predicted grid export/import rather than treating forecast derating or charging losses as export.
- Export attribution to storage-capacity, charge-power, or controller limitations.
- Headroom creation only when modeled storage capacity would actually cause meaningful export.
- Native backup reserve protection: the effective reserve floor is the greater of the configured planner floor and the current EcoFlow reserve.
- Advisory strategies: `HOLD`, `USE_DISCRETIONARY_LOADS`, `CREATE_HEADROOM`, `PRESERVE_FOR_RESILIENCE`, or `INSUFFICIENT_DATA`.
- UI configuration and options; no YAML is required for the integration itself.

### Planning priorities

The strategy engine is ordered around these goals:

1. Minimize exported solar.
2. Minimize grid purchases over the long term rather than shifting purchases between days.
3. Preserve stored solar unless a physical storage-capacity constraint would otherwise create meaningful export.
4. Never recommend stationary-battery discharge during the protected daylight/solar period.
5. Respect storm protection and the effective backup-reserve floor.
6. Use flexible loads only when they are **reliably controllable** and a physical export opportunity exists.

The reserve is a floor, not a routine overnight target.

### What changed from v0.1.7

v0.1.7 incorrectly used the `harvest_capture_factor` as though uncaptured forecast energy must be exported. For example, a factor of `0.88` mechanically turned 12% of modeled solar surplus into export even when the batteries had ample headroom and the real-time controller could absorb the surplus.

v0.1.8 removes that assumption. `harvest_capture_factor` is retained only for upgrade compatibility and is ignored by the physical forecast. Charge efficiency lowers stored battery energy but does **not** become grid export.

Predicted export now exists only when the simulated house + battery/controller system cannot absorb the modeled solar.

### Fast-controller mirroring

When an `ecoflow_solar_surplus` config entry is installed, Energy Planner reads its current options and the hardware min/max/step attributes from the configured charging-power entity. The planner does not issue commands to that integration; it only mirrors the policy for forecasting.

If the surplus controller is absent or not in control mode, the planner does not optimistically assume that AC surplus will be captured by it.

### EV / flexible-load safety

Solar EV advice is **disabled by default**. A large EV load without reliable start/stop control can easily turn a small predicted export into substantial grid import.

Enable `Allow EV solar-surplus recommendations` only after reliable EV start/stop control exists. Even then, EV/flexible-load advice is based on physically modeled export, not forecast derating.

### Advisory-only control model

v0.1.8 does **not** automatically change EcoFlow operating mode, discharge the battery, grid-charge the battery, lower the backup reserve, or start the EV. It publishes planning intent and supporting metrics for validation first.

## Forecast assumptions

The planner deliberately distinguishes what is known from what must be approximated:

- **Known/mapped:** daily solar forecast, corrected remaining solar, current solar power, forecast peak time, sunrise/sunset, representative house load, battery SOCs/capacities, charge limit, reserve, and fast-controller settings.
- **Full-day shape:** with only a daily energy total and a peak time, the planner uses an energy-conserving triangular solar curve. This is intentionally simple; inventing a more detailed cloud curve without time-series input would create false precision.
- **House load:** full-day forecasts use the configured representative/base-load sensor as a constant load. Intraday forecasts use the mapped expected load remaining to sunset.
- **Controller response:** the real controller reacts much faster than the forecast interval, so each forecast interval converges the controller toward a quasi-steady command rather than simulating every two-second event.
- **Weather/cloud transients:** not predicted independently beyond what is already present in the solar forecast/corrected-remaining inputs.

A future quality improvement can consume a genuine interval/hourly solar forecast and interval load forecast when those sources are available; that is preferable to adding arbitrary shape factors.

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
- solar forecast for today and next day
- optional live sunset-projection inputs
- optional forecast peak-time sensors
- expected/base house load power sensor
- optional EV SOC and location entities

Options include:

- minimum reserve percentage
- EV target SOC
- EV solar-surplus advisory opt-in
- AC-to-battery charge efficiency
- minimum physically predicted export before recommending a flexible load
- fallback preferred grid import if the fast surplus controller is unavailable

Legacy `strong_solar_kwh` and `harvest_capture_factor` values may remain in upgraded config entries for compatibility, but they are not used to manufacture export or to trigger EV charging in v0.1.8.

## Safety model

Priority is deliberately conservative:

1. Native device safety / storm protection
2. Current or configured reserve floor, whichever is higher
3. Preserve stored solar by default
4. Reduce physical future solar export
5. Flexible-load advice only for controllable loads
6. Create battery headroom only when capacity would otherwise cause meaningful export

If required planning inputs are unavailable, the strategy reports `INSUFFICIENT_DATA` rather than recommending discharge.
