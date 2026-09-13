# Home Assistant Energy Planner

Forecast-aware battery headroom and solar-export planning for Home Assistant.

## v0.1.10 scope

Energy Planner is the slow, advisory planning layer. The fast EcoFlow Solar Surplus integration remains responsible for real-time surplus capture.

The planner is intentionally split into two layers:

1. **Physical forecast:** what the house, solar array, batteries, charge limits, and verified charging-power capability can physically absorb.
2. **Confidence layer:** whether historical forecast performance supports taking a consequential action such as intentionally creating battery headroom.

This separation keeps forecast uncertainty from being misrepresented as grid export and prevents a single deterministic forecast from triggering unnecessary battery discharge.

## Planning priorities

The strategy engine is ordered around these goals:

1. Minimize exported solar.
2. Minimize grid purchases over the long term rather than merely shifting purchases between days.
3. Preserve stored solar unless measured evidence supports creating headroom to avoid otherwise unavoidable export.
4. Never recommend stationary-battery discharge during the protected daylight/solar period.
5. Respect storm protection and the higher of the configured planner reserve and the current native EcoFlow reserve.
6. Use flexible loads only when they are reliably controllable and a physical export opportunity exists.

The reserve is a floor, not a routine overnight target.

## Physical forecast model

v0.1.9 removed the earlier controller-loop and generic-capture-factor assumptions. v0.1.10 keeps that physical model.

During the protected solar window:

- Solar serves house load first.
- House-load deficit is supplied by the grid; the stationary battery does not discharge.
- Only true AC solar surplus is available for stationary-battery charging.
- The planner never manufactures grid-to-battery energy from the controller's preferred-import target.
- Charge-efficiency losses reduce stored battery energy; they are never reclassified as grid export.
- Routine fast-controller leakage is assumed to be zero until measured history provides evidence for an empirical residual model.

Forecast export exists only when solar surplus cannot physically be absorbed because:

- the capture controller is unavailable/not in control mode;
- aggregate charging-power capability is exceeded; or
- battery storage capacity / charge limit is exhausted.

The planner reads the installed `ecoflow_solar_surplus` integration to determine whether capture control is active and to obtain the effective per-DPU charging-power ceiling. It does **not** try to predict the controller's second-by-second feedback trajectory.

## Solar/load shape assumptions

The planner deliberately avoids false precision:

- **Full-day solar:** an energy-conserving triangular curve uses the daily forecast total plus forecast peak time. Without a real interval solar forecast, inventing cloud detail would be less trustworthy.
- **Full-day house load:** the configured representative/base-load sensor is used as the planning load.
- **Live remaining-day forecast:** corrected solar remaining, current production, expected load to sunset, and current battery state drive the live sunset projection.
- **Battery bank:** the three DPU stacks are simulated separately using configured capacity weights, so one stack can become full before the others.

## Confidence calibration

v0.1.10 adds persistent, evidence-based calibration for headroom decisions.

### Daylight forecast-error history

Before sunrise the planner stores its predicted battery stored-energy gain for the upcoming solar window. At sunset it compares that prediction with the actual stored-energy gain.

Each valid observation records:

- predicted stored-energy gain;
- actual stored-energy gain;
- absolute error; and
- relative error.

A negative relative error means the planner predicted more stored solar than actually materialized.

### Overnight battery-depletion history

The planner also records the natural stored-energy decrease between sunset and the next sunrise. This measures how much battery headroom the house naturally creates overnight before any intentional headroom action.

Nights associated with an active planner headroom-release request are excluded from natural overnight calibration so deliberate discharge is not mistaken for normal household depletion.

### Minimum evidence

The planner requires at least:

- **3 valid daylight observations**, and
- **3 valid overnight observations**

before confidence-based headroom creation can become actionable.

Until then:

- nominal capacity/export risk remains visible;
- the strategy stays conservative;
- recommended additional discharge is zero; and
- `Headroom Release Requested` remains off.

After 10 observations in each stream, the planner uses empirical distribution tails instead of a single historical extreme.

## No-regret headroom rule

The economics are asymmetric: unnecessary battery discharge can lead to later grid purchases, while accepting some export during an uncertain forecast only loses otherwise-uncredited solar.

For that reason, the confidence layer intentionally requires overlap between two conservative conditions:

- a **lower empirical daylight stored-solar outcome**; and
- an **upper empirical natural overnight depletion outcome**.

Natural overnight discharge creates headroom without intentional cycling, so the planner only recommends extra discharge that remains necessary even after allowing for a relatively high observed natural overnight headroom contribution.

The confidence-adjusted recommendation is therefore normally smaller than the nominal point-forecast shortfall and can be zero even when the nominal point forecast predicts a full battery.

## Baseline vs planned forecast

v0.1.10 explicitly separates:

- **Baseline / unmitigated export:** what the point forecast predicts before any planner headroom action.
- **Planned export:** what remains after the confidence-adjusted action, if one is justified.

This prevents a strategy explanation such as "10 kWh would otherwise export" from appearing beside an unexplained zero-export sensor.

## Key diagnostics

The integration exposes, among others:

- Forecast Calibration Status
- Confidence Headroom Action Ready
- Daylight Calibration Samples
- Overnight Calibration Samples
- Daylight Stored-Energy Forecast MAE / MAPE
- No-Regret Headroom Factor
- Median Overnight Battery Depletion Rate
- No-Regret Overnight Battery Depletion Rate
- Baseline Unmitigated Export Today / Next Day
- Baseline Capacity-Limited Export Today / Next Day
- Nominal Required Headroom / Margin / Shortfall
- No-Regret Required / Available Headroom / Shortfall
- Expected Natural Overnight Battery Depletion
- No-Regret Natural Overnight Headroom Allowance
- Confidence-Adjusted Discharge Before Next Day
- Planned Solar Export Today / Next Day
- Capacity-Limited, Power-Limited, and Control-Unavailable export components
- Forecast Grid-to-Battery energy
- Peak Unavoidable Export and duration

Every material export forecast should therefore have an auditable physical cause.

## EV / flexible-load safety

Solar EV advice is **disabled by default**. A large EV load without reliable start/stop control can easily turn a small export opportunity into material grid import.

Enable EV solar-surplus recommendations only after reliable EV start/stop control exists. The Lexus should not be used merely because a point forecast predicts modest excess solar.

## Advisory-only control model

Energy Planner does not directly:

- change EcoFlow operating mode;
- discharge batteries;
- grid-charge batteries;
- lower the native backup reserve; or
- start the EV.

It publishes planning intent and `Headroom Release Requested`. Any executor/automation consuming that intent should continue to enforce its own device-safety checks.

## Installation with HACS

Until this repository is added to the default HACS store, add it as a custom repository:

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/chrisj500/home-assistant-energy-planner` with category **Integration**.
3. Download **Home Assistant Energy Planner**.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add Integration** and search for **Energy Planner**.

## Configuration

The setup flow asks for the core battery and forecast entities, including:

- three battery SOC sensors;
- battery capacity weights, such as `3,2,3`;
- total battery capacity in kWh;
- charge-limit number entity;
- backup-reserve number entity;
- storm-warning binary sensor;
- solar forecast for today and next day;
- optional live sunset-projection inputs;
- optional forecast peak-time sensors;
- expected/base house load power sensor; and
- optional EV SOC and location entities.

Options include:

- minimum reserve percentage;
- EV target SOC;
- EV solar-surplus advisory opt-in;
- AC-to-battery charge efficiency;
- minimum physically predicted export before considering a flexible-load action; and
- fallback preferred grid import for compatibility when the fast surplus controller is unavailable.

Legacy `strong_solar_kwh` and `harvest_capture_factor` values may remain in upgraded config entries for compatibility, but they do not manufacture export or control confidence-based headroom decisions.

## Safety model

Priority is deliberately conservative:

1. Native device safety / storm protection
2. Current or configured reserve floor, whichever is higher
3. Preserve stored solar by default
4. Model physical export accurately
5. Require measured forecast evidence before intentional headroom creation
6. Flexible-load advice only for reliably controllable loads

If required planning inputs are unavailable, the strategy reports `INSUFFICIENT_DATA` rather than recommending discharge.
