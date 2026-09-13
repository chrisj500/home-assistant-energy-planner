# Home Assistant Energy Planner

Forecast-aware battery headroom and solar-export planning for Home Assistant.

## v0.1.11 scope

Energy Planner is the slow, advisory planning layer. The fast EcoFlow Solar Surplus integration remains responsible for real-time surplus capture.

v0.1.11 keeps the v0.1.10 physical and confidence models intact and adds two deliberately conservative changes:

1. **Fresh pre-sunrise calibration snapshots.** The pending daylight forecast is refreshed during the pre-sunrise period and frozen only when the daylight sample actually starts.
2. **Optional Forecast.Solar shadow evaluation.** A paid/trial API key can expose the richer interval forecast and Professional weather data for comparison, without changing any authoritative planning decision.

The planner remains fully functional with no Forecast.Solar API key or subscription. Missing, invalid, expired, rate-limited, or unavailable enhanced data falls back to the baseline planner for that refresh.

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

## Baseline solar/load shape assumptions

The baseline model deliberately avoids false precision:

- **Full-day solar:** an energy-conserving triangular curve uses the daily forecast total plus forecast peak time.
- **Full-day house load:** the configured representative/base-load sensor is used as the planning load.
- **Live remaining-day forecast:** corrected solar remaining, current production, expected load to sunset, and current battery state drive the live sunset projection.
- **Battery bank:** the three DPU stacks are simulated separately using configured capacity weights, so one stack can become full before the others.

This remains the authoritative behavior even when the optional Forecast.Solar shadow is enabled.

## Optional Forecast.Solar shadow evaluation

A Forecast.Solar API key is optional. Add it in Energy Planner options only if you want to evaluate a paid/trial Forecast.Solar account.

Without a valid key:

- all normal Energy Planner forecasts continue to run;
- strategy and headroom logic are unchanged;
- the Forecast.Solar enhancement status reports baseline fallback; and
- no paid feature is required for safe operation.

With a valid key, v0.1.11 requests the authenticated Forecast.Solar production estimate and evaluates its interval `watts` series in **shadow mode**.

### Why shadow mode

The paid interval forecast is not allowed to drive strategy, discharge recommendations, or `Headroom Release Requested` in v0.1.11. We first want measured evidence that it improves forecast accuracy enough to justify a subscription.

The shadow simulator uses the same:

- current battery SOCs and bank capacities;
- charge limit;
- AC-to-battery efficiency;
- expected house load to sunset; and
- EcoFlow Solar Surplus physical charging capability

as the baseline live projection.

To isolate the value of the richer **time shape**, the paid interval curve is scaled to the existing corrected remaining-solar energy total. This means a comparison between baseline and shadow is primarily testing whether the real 15/30-minute Forecast.Solar shape improves the outcome versus the baseline geometric curve, rather than silently replacing the existing local solar-bias correction.

Shadow diagnostics include:

- Forecast.Solar enhancement/fallback status
- detected account type
- interval resolution
- forecast horizon
- number of interval points
- paid raw remaining solar to sunset
- shadow projected sunset SOC
- shadow sunset-SOC delta versus baseline
- shadow battery charge to sunset
- shadow daylight grid import
- shadow export
- shadow energy scale factor

### Professional weather data

The verified Professional weather endpoint is also queried conservatively and remains diagnostic-only. Exposed values include:

- current Forecast.Solar sky factor; the provider defines `1` as clear sky;
- average sky factor for the next six hours; and
- forecast temperature.

The extra Professional capabilities for controllable-load time windows and theoretical clear-sky production are useful candidates for later evaluation, but v0.1.11 does **not** guess or depend on unverified endpoint/response schemas. They can be added after the live trial confirms their exact contract.

### Failure behavior

Enhanced Forecast.Solar failures must never make the baseline planner unavailable. Examples that cause transparent fallback include:

- no API key;
- malformed API key;
- rejected/expired key;
- rate limiting;
- provider/network error;
- missing interval forecast data; or
- missing Forecast.Solar site/plane geometry.

The API key is never exposed as a sensor value.

## Confidence calibration

v0.1.10 introduced persistent, evidence-based calibration for headroom decisions; v0.1.11 preserves that model.

### Daylight forecast-error history

Before sunrise the planner stores its predicted battery stored-energy gain for the upcoming solar window. At sunset it compares that prediction with the actual stored-energy gain.

v0.1.11 improves the sampling boundary: while still before sunrise, the pending forecast is refreshed with the latest valid prediction. Once daylight starts and the starting battery energy is recorded, that forecast is frozen for the day. This prevents calibration from scoring a stale forecast captured shortly after midnight when a newer pre-sunrise forecast was available.

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

The economics are asymmetric: unnecessary battery discharge can lead to later grid purchases, while accepting some export during an uncertain forecast only loses the value difference between self-consumed solar and export compensation.

For that reason, the confidence layer intentionally requires overlap between two conservative conditions:

- a **lower empirical daylight stored-solar outcome**; and
- an **upper empirical natural overnight depletion outcome**.

Natural overnight discharge creates headroom without intentional cycling, so the planner only recommends extra discharge that remains necessary even after allowing for a relatively high observed natural overnight headroom contribution.

The confidence-adjusted recommendation is therefore normally smaller than the nominal point-forecast shortfall and can be zero even when the nominal point forecast predicts a full battery.

## Baseline vs planned forecast

The planner explicitly separates:

- **Baseline / unmitigated export:** what the point forecast predicts before any planner headroom action.
- **Planned export:** what remains after the confidence-adjusted action, if one is justified.

This prevents a strategy explanation such as "10 kWh would otherwise export" from appearing beside an unexplained zero-export sensor.

## EV / flexible-load safety

Solar EV advice is **disabled by default**. A large EV load without reliable start/stop control can easily turn a small export opportunity into material grid import.

Enable EV solar-surplus recommendations only after reliable EV start/stop control exists. The Lexus should not be used merely because a point forecast predicts modest excess solar.

Forecast.Solar Professional weather or interval data does not change this rule.

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
- expected/base house load power sensor;
- optional EV SOC and location entities; and
- an optional Forecast.Solar API key used only for shadow evaluation.

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
7. Treat paid Forecast.Solar data as optional evidence until it proves useful

If required baseline planning inputs are unavailable, the strategy reports `INSUFFICIENT_DATA` rather than recommending discharge. Optional Forecast.Solar failures alone do not make baseline planning insufficient.
