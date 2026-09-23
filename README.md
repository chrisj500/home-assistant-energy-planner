# Home Assistant Energy Planner

## v0.1.31 HVAC room layout refinement

Dashboard v14 restores the circular thermostat face and arranges indoor climate
by physical room: Main Bedroom (Left/Right HomePod average) and Guest Bedroom on
the first row, Living Room and an equal-weight House Average on the second.
Temperature and humidity are shown together, and the house average counts the
Main Bedroom only once.

The dashboard and HVAC learner now resolve both current and persisted HomePod
entity-ID forms rather than assuming Home Assistant renamed existing entities.
Climate entities that provide a mode but omit `hvac_action` now show the mode
instead of being labeled unavailable. Global storm protection is no longer
repeated beneath every Battery Outlook date. See
[release notes](RELEASE_NOTES_0.1.31.md).

## v0.1.30 HVAC dashboard and observability

Dashboard v13 fixes the HomePod room entity mappings, gives the HVAC learner an
explicit readiness target of **36 clean five-minute samples across at least 3
days**, and redesigns the thermostat card around the Aqara-style black face:
upper arc, large setpoint, measured temperature/humidity, plus/minus marks and
mode/fan/home indicators.

HVAC electrical power now treats a numeric Home Assistant circuit state as valid
until Home Assistant marks that source `unknown` or `unavailable`; an
unchanged `0 W` state no longer becomes unavailable merely because its state
timestamp is older than five minutes. Daily integration still excludes actual
unavailable periods and exposes source/coverage diagnostics.

Forecast reliability now exposes its action-learning prerequisites directly:
**3 scored sunset forecasts and 3 valid overnight calibration records**. Storm
safety also distinguishes `active` from `sensor_unavailable` and reports the
configured storm entity and raw state. See
[release notes](RELEASE_NOTES_0.1.30.md).

## v0.1.28 HVAC-aware safety and learning

Enable **Read-only HVAC learning and safety holds** in Energy Planner options,
confirm the supplied entity mappings, and inspect **HVAC Model Status**. Its
attributes expose room-weighted indoor conditions, circuit signatures, anomaly
reasons and hourly shadow estimates. No thermostat control is enabled. The
experimental HVAC forecast does not replace the battery load model until its
accuracy and non-HVAC baseline have been validated. See the explicit scope and
validation limits in [release notes](RELEASE_NOTES_0.1.28.md).

## v0.1.27 dashboard reserve-floor guard

The Battery Outlook now independently clamps displayed sunset ranges to the
Effective Reserve Floor entity. This also protects the dashboard from stale
pre-v0.1.26 forecast attributes after an integration or dashboard update. See
[release notes](RELEASE_NOTES_0.1.27.md).

## v0.1.26 dashboard and reserve-floor correction

Battery Outlook scenario ranges now respect the configured reserve while the
planner assumes grid-connected operation. The compact four-day card no longer
prints overlapping range captions, and reliability hold states retain the
yellow stoplight graphic. See [release notes](RELEASE_NOTES_0.1.26.md).

## v0.1.25 reliability revision

Rolling forecasts now expose SOC scenario ranges and horizon-specific confidence.
Unstable, uncalibrated, or stale forecasts withhold discretionary advice. EV advice
requires conservative headroom need, repeated provider confirmations, and sustained
measured solar surplus. Automatic stationary-battery headroom release is disabled
in this revision. See [release notes](RELEASE_NOTES_0.1.25.md) for thresholds,
learning requirements, dashboard changes, and validation limits. Earlier version
descriptions below are historical; the reliability checks take precedence.

Forecast-aware battery headroom, solar-export, and flexible-load planning for Home Assistant.

## v0.1.12 scope

Energy Planner is the slow, advisory planning layer. The fast EcoFlow Solar Surplus integration remains responsible for real-time surplus capture.

v0.1.12 preserves the v0.1.11 physical, calibration, and Forecast.Solar shadow models and adds a new **rolling EV/headroom advisory**. The goal is not to maximize stationary-battery SOC. The planner looks ahead for the first future day on which strong solar is likely to run out of stationary-battery headroom, then identifies a solar-rich window in which charging an EV can preserve useful headroom for that later harvest.

The new rolling advisory is deliberately non-authoritative and does not actuate the Lexus, EcoFlow batteries, or charger.

The planner remains fully functional with no Forecast.Solar API key or subscription. Missing, invalid, expired, rate-limited, or unavailable paid data simply disables the rolling paid-forecast advisory for that refresh; baseline planning continues.

## Planning priorities

1. Minimize exported solar.
2. Minimize grid purchases over the long term rather than merely shifting purchases between days.
3. Preserve stored solar unless a specific physical/economic reason justifies using it.
4. Never discharge the stationary battery during the protected daylight/solar window.
5. Use flexible loads when doing so prevents a future storage-headroom problem, not merely because solar is currently available.
6. Respect storm protection and the higher of the configured planner reserve and the current native EcoFlow reserve.

The reserve is a floor, not a routine overnight target.

## Physical forecast model

During the protected solar window:

- solar serves house load first;
- house-load deficit is supplied by the grid;
- the stationary battery does not discharge;
- only true AC solar surplus may charge the battery;
- charge-efficiency losses reduce stored energy and are never reclassified as export; and
- forecast export exists only when the capture controller is unavailable, charging power is insufficient, or storage capacity/charge limit is exhausted.

The planner reads the installed `ecoflow_solar_surplus` integration to obtain capture availability and effective per-DPU charging power. It does not attempt to reproduce the fast controller's second-by-second feedback loop.

## Stable planning load

The existing baseline Today/Next Day model still uses its configured representative/base-load entity and remains authoritative for existing strategy and `Headroom Release Requested` outputs.

The v0.1.12 **rolling** planner intentionally avoids using an instantaneous EV-subtracted load as its primary multi-day load estimate. If available, it blends:

- 45% of the 3-hour smoothed non-EV load; and
- 55% of the 24-hour smoothed non-EV load.

By default it looks for `sensor.forecast_base_load_3h_average` and `sensor.forecast_base_load_24h_average`; either may be overridden in Energy Planner options. If the smoothed inputs are unavailable, the configured base-load entity is used as a fallback.

This prevents an actively charging EV from collapsing the rolling planning load toward zero because of momentary measurement timing differences.

## Optional Forecast.Solar enhanced data

A Forecast.Solar API key is optional. With a valid keyed account, Energy Planner continues to expose the v0.1.11 same-day scaled shadow comparison and, in v0.1.12, also evaluates the provider's paid interval curve directly across the available multi-day horizon for rolling advisory purposes.

The same-day shadow remains scaled to the locally corrected remaining-energy total so it isolates the value of the richer interval **shape**. The rolling EV model is explicitly labeled `forecast_solar_paid_raw_rolling_v1` and uses the paid interval forecast as an advisory scenario rather than silently replacing the baseline planner.

Enhanced Forecast.Solar failures never make baseline planning unavailable.

## Rolling EV/headroom advisory

The rolling model simulates sequential solar days, carrying the three stationary-battery bank SOCs forward from one day to the next. Between solar windows it applies the empirically learned median overnight depletion rate, bounded by the effective reserve floor. During daylight the stationary battery remains protected from discharge.

For every modeled day it calculates physical solar surplus, available stationary headroom, required stored headroom, capacity/power-limited export, grid import, and ending SOC. The first day with material capacity/headroom risk becomes the **Rolling Headroom Risk Date**.

If the EV is home and can still accept energy, the planner then works backward from that risk and searches the available solar windows for a contiguous charging interval. It recommends only enough EV wall energy to address the modeled stationary-headroom problem, capped by the EV's remaining energy-to-target.

A charging window is exposed only when at least 80% of the modeled EV charging energy can be supplied by solar surplus. Otherwise the status explains that the candidate is too grid-heavy.

The advisory exposes, among other values:

- rolling planning load and its source;
- first headroom-risk date and modeled shortfall;
- risk-day projected SOC and capacity export;
- EV available energy to target;
- learned/fallback EV charging power and sample count;
- recommended EV charging energy;
- recommended window start/end;
- modeled solar and grid energy in that window; and
- stationary-battery headroom preserved by the recommended solar charging.

### EV energy and charging power

Configure the EV SOC entity and target SOC. `EV wall energy from 0-100% SOC` is an adjustable AC-wall-energy estimate used to translate SOC percentage into how many kWh the car can still accept. The default is 20 kWh and should be replaced with a value appropriate for the actual vehicle/charging history.

An EV circuit/charger power entity may also be configured. While charging power is at least 500 W, the integration retains recent samples in memory and uses their median as the learned charging rate. Until samples are available, it uses the configurable fallback charging power (default 6600 W).

These values are advisory estimates. No charging action is executed by the integration.

## Forecast.Solar same-day shadow evaluation

The v0.1.11 comparison remains available. It uses the authenticated interval `watts` series to compare the richer production shape against the baseline live sunset projection while holding the corrected remaining-energy total constant.

Diagnostics include account type, interval resolution, horizon, interval point count, paid raw remaining energy, shadow sunset SOC, battery charge, daylight grid import/export, and scaling factor. Professional weather data remains diagnostic-only.

## Confidence calibration

Before sunrise the planner stores its predicted battery stored-energy gain for the upcoming solar window. The pending prediction refreshes until daylight starts, then freezes once the daylight sample records starting stored energy. At sunset the predicted and actual gains are compared.

Natural overnight stored-energy depletion is also learned from sunset-to-sunrise observations. Nights associated with an active planner headroom-release request are excluded from natural overnight calibration.

At least 3 valid daylight and 3 valid overnight observations are required before confidence-based stationary-battery headroom creation becomes actionable. After 10 observations in each stream, empirical distribution tails replace the small-sample worst observed values.

## EV / flexible-load safety

The rolling EV feature is an **advisory scheduler**, not an EVSE controller. It does not assume that the Lexus can be reliably started/stopped or modulated automatically. The user may use the recommendation manually; any future executor should enforce its own device-safety and availability checks.

The older EV solar-surplus advisory option remains separate and disabled by default.

## Advisory-only control model

Energy Planner does not directly:

- change EcoFlow operating mode;
- discharge stationary batteries;
- grid-charge stationary batteries;
- lower the native backup reserve; or
- start/stop the EV.

It publishes planning intent and diagnostics. `Headroom Release Requested` remains governed by the existing confidence-calibrated baseline policy, not the new rolling EV advisory.

## Installation with HACS

Until this repository is added to the default HACS store, add it as a custom repository:

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/chrisj500/home-assistant-energy-planner` with category **Integration**.
3. Download **Home Assistant Energy Planner**.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add Integration** and search for **Energy Planner**.

## Configuration

Core setup includes the three battery SOC sensors, capacity/weights, charge limit, backup reserve, storm warning, solar forecasts, live projection inputs, representative house load, and optional Forecast.Solar API key.

For the v0.1.12 rolling EV advisory, optionally configure:

- smoothed non-EV 3-hour load average sensor;
- smoothed non-EV 24-hour load average sensor;
- EV SOC;
- EV location tracker;
- EV circuit/charger power sensor;
- EV target SOC;
- EV wall energy from 0-100% SOC; and
- EV fallback AC charging power.

If the standard smoothed-load entity IDs exist, the rolling model can discover them without explicit configuration.

## Safety model

Priority is deliberately conservative:

1. Native device safety / storm protection
2. Current or configured reserve floor, whichever is higher
3. Preserve stored solar by default
4. Model physical export accurately
5. Use natural overnight depletion before intentional stationary-battery cycling
6. Use EV/flexible load only when a future headroom need is identified and the candidate charging window is predominantly solar
7. Treat paid Forecast.Solar data as optional advisory evidence

If required baseline planning inputs are unavailable, the baseline strategy reports `INSUFFICIENT_DATA`. Optional Forecast.Solar or rolling-EV failures alone do not make baseline planning insufficient.
# HVAC overview (v0.1.29)

The v12 example dashboard includes a thermostat dial, reported HVAC action, room temperatures, combined condenser/blower power, and measured-power-derived daily electricity with coverage. Update `dashboards/energy-planning.yaml` separately from the HACS integration; existing dashboards are not automatically replaced. See [installation and measurement details](RELEASE_NOTES_0.1.29.md).
