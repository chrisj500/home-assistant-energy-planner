# AC solar learning — v0.1.41

## What is implemented

A local, lightweight statistical residual learner runs in shadow mode. It learns
hourly Forecast.Solar errors for comparable local hours, forecast lead times, and
forecast cloud categories. This is a first-stage learning baseline, not a standalone
weather-to-solar model or a transformer. It cannot influence planner decisions.

Every hour, freeze predictions for the next 24 complete UTC hours, along with the
local date/hour, issuance time, provider retrieval time, and available weather.
The furthest hour ends up to 25 hours after issuance. Save raw paid, v0.1.40 live
adjusted, and learned predictions separately. Only completed, valid observations
available before issuance enter training. Repeated predictions of the same target
hour count as one training observation within each group.

Because target windows start at the next full hour, most of the live adjustment
has already decayed for these comparisons. This release does not yet score the
current partial hour, whole remaining-day energy, or battery SOC as ML outcomes.

The learner requires eight distinct target hours across at least seven days within
each hour/lead/cloud group. It shrinks the median residual by n/(n+20), bounds the
adjustment to +/-25% of provider energy, and produces nonnegative energy. Until
then, the learned prediction equals the paid prediction and is explicitly marked
untrained. Warm-up rows are excluded from the trained-only comparison.

## Inputs and configuration

All settings are in Energy Planner's integration options. Collection is enabled
by default; it can be disabled. It uses the already-configured actual solar power
entity, accepting W or kW. Use gross PV AC production, never grid export, household
consumption, or battery power. Enphase AC production is the learning target.

Optional inputs:

- Ecowitt radiation: default sensor.gw3000b_solar_radiation, W/m² or W/m2.
- Ecowitt outdoor temperature: default sensor.gw3000b_outdoor_temperature, °C or °F.
- Lifetime production energy: Wh or kWh, auto-detected only when exactly one
  Enphase lifetime-production entity exists, otherwise select it explicitly.
- Home Assistant sun elevation/azimuth are saved when available.

Lifetime energy is currently a reset cross-check and archived observation, not the
training energy source. Actual hourly energy is integrated from validated AC power.
Select radiation/temperature entities with different IDs through integration options.
Missing optional inputs are reported and do not prevent baseline collection.

The paid interval forecast is the required forecast baseline. Existing paid weather
responses provide cloud fraction and forecast temperature when available. No extra
API requests, API key, or weather subscription is added. Observed Ecowitt conditions
are archived as observations at issuance, never substituted for tomorrow's weather.
These observations are collected for later models; the first residual model uses
hour, lead, and forecast cloud category only.

## Data quality and persistence

- Reject unavailable, nonnumeric, nonfinite, negative power, unsupported units, and
  readings not reported within five minutes.
- Do not integrate across sample gaps longer than three minutes or across restarts.
- Flag zero production when valid local radiation is at least 100 W/m². This is a
  conservative suspicious-data exclusion, not proof of a telemetry fault.
- Lifetime-meter resets invalidate the affected segment.
- Accept outcomes only with at least 99% hour coverage and no invalid segments.
  Missing intervals are never filled with zero. Small permitted gaps remain visible
  through coverage; the incomplete energy is not extrapolated.
- Do not issue from interval responses older than two hours or targets outside the
  returned curve, or across gaps in forecast points longer than two hours.
- Weather rows must be within 30 minutes of target start and fetched successfully
  within two hours. Missing cloud data uses its own unknown category.
- Models and datasets persist via HA storage every five minutes and on issuance.
  An unclean restart can lose up to five minutes of recent observations; the gap is
  excluded. Production/site mapping changes reset incompatible learning with a reason.

Retain 90 days of actual hours and issued outcomes (maximum 60,000 scored rows).
Diagnostics export the dataset; large exports grow with collection. Source settings,
code, tests, and retention policy live in GitHub. Measurements and learned state live
privately in Home Assistant, not in the public repository. No training data is uploaded.

## Evaluation and limits

Status attributes include raw, live-adjusted and learned MAE and signed bias by lead
bucket, both all-row and trained-only comparisons, sample counts, unique target
hours, usable days, and exclusions. Compare trained models on the same frozen target
rows; many predictions on one day are not independent days of evidence.

No confidence intervals or automatic model promotion are implemented. Shadows
always remain advisory diagnostics. Clipping, curtailment, snow, and sensor problems
cannot always be distinguished from aggregate AC readings alone. The first seven
days is a minimum evidence gate, not an accuracy guarantee; comparable groups can
take weeks to populate. Ninety-day history cannot establish annual seasonal accuracy.

For a later independent model we still need verified roof-group capacity/orientation/
tilt, inverter AC limits, and an archived numerical weather forecast with future
irradiance and clouds. Existing Forecast.Solar site settings can supply geometry
where correctly configured. Ecowitt cannot supply future weather by itself. Evaluate
that expansion only after the baseline collection and scoring are working reliably.
