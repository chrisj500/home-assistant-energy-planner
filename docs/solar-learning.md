# AC solar learning — residual models, horizon blend, and persistence

## What is implemented

Three local residual learners and two challengers run in parallel shadow mode and cannot influence
planner decisions.

- **v3 control model** groups Forecast.Solar error by local hour, lead-time bucket,
  and categorical cloud bin (clear/mixed/cloudy/unknown).
- **v4 continuous model** learns from nearby historical conditions using continuous
  cloud fraction, local hour, continuous lead time, raw Forecast.Solar energy,
  forecast temperature, seasonal day, and—only for sub-3-hour targets—issuance-time
  Ecowitt radiation and live PV power.
- **v4.1 tuned continuous model** preserves continuous cloud generalization while
  adding target-hour solar elevation/azimuth, tighter lead-time locality and
  correction authority that grows only with evidence and demonstrated prior
  out-of-sample performance.

v4 and v4.1 are deliberately small, inspectable statistical models rather than
neural networks. Their purpose is to learn how this installation behaves under
changing cloud conditions without fragmenting evidence into dozens of tiny
categorical buckets.

Every hour, freeze predictions for the next 24 complete UTC hours, along with the
local date/hour, issuance time, provider retrieval time, and available weather.
The last target begins less than 24 hours after issuance and ends up to 25 hours later. Save raw paid, v0.1.40 live
adjusted, and learned predictions separately. Only completed, valid observations
available before issuance enter training. Repeated predictions of the same target
hour count as one training observation within each group.

Because target windows start at the next full hour, most of the live adjustment
has already decayed for these comparisons. This release does not yet score the
current partial hour, whole remaining-day energy, or battery SOC as ML outcomes.

The v3 learner requires three distinct target hours across at least three days
within each hour/lead/cloud group. It shrinks the median residual by n/(n+20),
bounds the adjustment to +/-25% of provider energy, and produces nonnegative
energy. Until then, the v3 learned prediction equals the paid prediction and is
explicitly marked untrained.

The v4 learner does not require an exact hour/cloud-bin match. For every candidate
it selects at most one comparable forecast from each historical target hour, ranks
those observations by continuous feature distance, and uses up to 24 nearest
neighbors. It begins shadow evaluation with at least six unique target hours across
three days and at least three effective weighted neighbors. A robust weighted
median of relative Forecast.Solar residuals is regularized toward zero and bounded
to +/-40%. Warm-up rows are excluded from v4 trained-only comparisons.

The v4.1 learner keeps the same minimum evidence gate but changes how similarity
and correction authority work:

- lead-time neighbors must fall within +/-1.5 hours for 0–3h targets, +/-3 hours
  for 3–12h targets, or +/-4 hours for 12–24h targets;
- target-midpoint solar elevation and azimuth are frozen for every forecast row
  and weighted more strongly than wall-clock hour;
- continuous cloud fraction remains the dominant weather feature;
- early residuals are regularized more strongly toward Forecast.Solar;
- the maximum correction starts small and grows with effective-neighbor count,
  number of represented days, and the model's own prior frozen out-of-sample
  performance for that horizon;
- medium-horizon correction authority is multiplied by 0.65 and 12–24h authority
  by 0.40, while 0–3h retains full earned authority;
- even fully mature v4.1 corrections are hard-capped at +/-20%.

The original v4 output remains frozen as a separate comparator. v4.1 never
rewrites v4 historical predictions.

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
v3 uses hour, lead bucket, and forecast cloud category. v4 uses the numerical
forecast cloud fraction directly together with the other continuous features listed
above. v4.1 additionally derives target-midpoint solar geometry locally from the
Home Assistant site latitude/longitude and target timestamp; the site coordinates
are not added to diagnostics or the forecast rows. Observed Ecowitt radiation/live
PV are only used as issuance-time context for targets less than three hours away;
they are never treated as knowledge of future weather.

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

Status attributes include raw, live-adjusted, v3, v4 and v4.1 MAE/signed bias by
lead bucket, trained-only comparisons, sample counts, unique target hours, usable
days, and exclusions. Compare models on the same frozen target rows; many
predictions on one day are not independent days of evidence. v4 and v4.1 have
separate scorecards so their continuous-neighbor behavior can be compared directly
against Forecast.Solar and the v3 control.

No confidence intervals or automatic model promotion are implemented. All five
models remain advisory diagnostics. Clipping, curtailment, snow, and sensor problems
cannot always be distinguished from aggregate AC readings alone. v4/v4.1 reduce
categorical fragmentation but still depend on the quality of the underlying future
weather forecast. Ninety-day history cannot establish annual seasonal accuracy.

For a later independent model we still need verified roof-group capacity/orientation/
tilt, inverter AC limits, and an archived numerical weather forecast with future
irradiance and clouds. Existing Forecast.Solar site settings can supply geometry
where correctly configured. Ecowitt cannot supply future weather by itself. Evaluate
that expansion only after the baseline collection and scoring are working reliably.

## Horizon-specific blend (0.1.71)

The `horizon_weighted_blend` challenger combines raw Forecast.Solar, the live
adjustment, and currently trained v3/v4/v4.1 predictions. Members whose current
output is identical to another member are not given duplicate weight. The live
baseline is preferred at 0–3h; raw is the baseline at longer horizons. Persistence
is evaluated separately and is not a member of this first blend.

Weights use up to the latest 48 distinct target hours in the same horizon over
the previous 21 days. A target must have completed before issuance. The latest
issued prediction for each target is selected first, then every member is
evaluated on the same complete cohort. Nighttime raw forecasts below 0.05 kWh
are excluded, matching the existing scorecards. At least 12 targets across three
days are required. Historical warm-up predictions remain in this matched cohort;
their baseline fallback is the prediction that was actually frozen at issuance.

Member weights are proportional to inverse MAE with a 0.10 kWh error floor. They
are shrunk toward the baseline using `n/(n+24) * min(1, days/7)`, capped at 0.75.
The remaining weight belongs to the baseline. This avoids switching entirely to
a model after a few favorable hours. Each issued row saves exact weights,
matched-cohort MAEs, sample/day counts, and a fallback reason. Insufficient
evidence returns the horizon's baseline and is marked inactive.

## Short-term clear-sky persistence (0.1.71)

The `clear_sky_persistence` challenger scales the current measured AC power by
the ratio of future/current clear-sky horizontal irradiance. It uses the Haurwitz
shape `1098 * sin(elevation) * exp(-0.059 / sin(elevation))` and the existing
approximate NOAA-style solar geometry. This is a **horizontal irradiance proxy**,
not a site-specific roof or inverter model. Geometric rather than refracted solar
elevation is used; issuance below 10 degrees falls back. Array orientation,
shading, clipping and future cloud movement can still make this model wrong.

At each one-minute integration midpoint, persistence influence decays linearly
from 100% at issuance to zero two hours later. The remainder uses the frozen
provider curve. Future/current shape ratios are limited to 3.0. Valid zero
production can lower the forecast; suspicious zero readings already rejected by
the collector cannot be used. Invalid, negative, nonfinite, future-dated, or
over-five-minute-old observations, missing geometry, and incomplete provider
curves all fall back to raw with a reason. No new packages or API calls are needed.

Haurwitz reference: [pvlib clear-sky model documentation](https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.clearsky.haurwitz.html).

### Prospective evaluation and restart behavior

Both predictions are frozen on new pending rows after the residual-model outputs
are frozen. Existing history is preserved; neither challenger is retrospectively
backfilled. The blend may use existing scored history to estimate weights, but
its own scores begin only after newly issued targets resolve. Upgrading between
issuances can take up to an hour before the first new challenger forecasts.

All challenger scorecards compare raw/live/v3/v4/v4.1/blend/persistence on the
same prospective rows. Each has an `all` and an `active_only` cohort. Use the
persistence **active-only 0–3h** cohort to assess its value: later forecasts equal
raw and must not be counted as independent persistence successes. Scores remain
hourly and start at the next full UTC hour; current partial-hour/15-minute and
battery SOC scoring are not included. There are no accuracy guarantees or
automatic promotions.

Existing HA storage persists predictions, weights, reasons and outcomes; restart
does not recompute them. Source-identity resets and retention rules still apply.
Diagnostics include `blend` and `persistence` sections. New sensors expose each
model's status and active scored count, with its scorecard on the status sensor.
Operational forecasts, battery strategy, EV advice and equipment controls do not
consume either challenger's predictions.


## v4 historical shadow bootstrap

On the first v4-capable release, existing scored v3 history is replayed to freeze
v4 predictions for recent historical rows. This is an out-of-sample replay: a
historical candidate may only use target hours whose observations had already
finished before that candidate's original issuance timestamp. Future observations
are never backfilled into an earlier prediction. Backfill is capped at the most
recent 2,500 scored rows to avoid an expensive first-upgrade replay on very large
90-day datasets; all new forecasts are frozen with both v3 and v4 predictions at
issuance.


## v4.1 historical replay and performance gating

v4.1 receives its own chronological backfill. Historical target solar geometry is
derived from the fixed site location and target midpoint, then each v4.1 prediction
is frozen using only outcomes that had completed before that prediction's original
issuance time.

The correction cap is intentionally self-limiting. Before four prior trained
v4.1 targets exist in a horizon, only 25% of otherwise earned correction authority
is available. With four to seven prior trained targets the provisional authority
is 35%. Once at least eight exist, the model compares its own prior MAE with raw
Forecast.Solar for up to the most recent 24 frozen targets in that horizon. If
v4.1 has been worse than raw, correction authority is reduced by the cube of the
raw/v4.1 MAE ratio, with a 15% floor. If v4.1 is beating raw, it can earn the full
evidence- and horizon-limited cap.

This performance gate is evaluated from prior outcomes only. The target currently
being predicted never contributes to its own trust multiplier.
