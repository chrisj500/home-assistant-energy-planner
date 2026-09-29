# Energy Planner 0.1.66

## Add continuous weather-aware solar v4 shadow learner

Energy Planner now runs a second solar learner beside the existing v3 control
model. Both remain strictly shadow-only: neither can change the operational
Forecast.Solar curve, battery strategy, EV advice, or equipment controls.

### v3 remains the control

The existing v3 learner is unchanged. It learns a regularized Forecast.Solar
residual for exact local-hour / lead-bucket / cloud-category groups.

### v4 learns across nearby weather conditions

The new `continuous_weather_residual_v4` model replaces categorical cloud
matching with a continuous nearest-neighbor residual model. As-issued features
include:

- numerical forecast cloud fraction;
- local target hour;
- continuous forecast lead time;
- raw Forecast.Solar target-hour energy;
- forecast temperature;
- seasonal day;
- for targets under three hours away only, issuance-time Ecowitt radiation and
  live PV power as cloud-persistence context.

Cloud fraction is the dominant feature, so mixed and cloudy observations can
inform nearby cloudy forecasts without requiring an exact categorical bucket.

For each candidate, v4:

- considers only target hours that fully completed before the forecast was issued;
- lets each historical target hour contribute at most one comparable forecast;
- uses at most 24 nearest target-hour neighbors;
- requires at least 6 unique target hours across 3 days and at least 3 effective
  weighted neighbors before marking a prediction trained;
- learns a robust weighted median of relative Forecast.Solar residuals;
- regularizes early corrections toward zero;
- bounds shadow corrections to +/-40% of provider energy;
- always produces a nonnegative hourly prediction.

### Honest historical bootstrap

Existing retained v3 history is replayed on first v4 startup so shadow evaluation
can begin immediately instead of waiting weeks to refill the dataset.

The replay remains out-of-sample: every historical v4 prediction may use only
observations whose target hours had already completed before that prediction's
original issuance timestamp. Future outcomes cannot leak backward.

To keep startup work bounded on large retained histories:

- at most the most recent 2,500 scored rows are backfilled;
- neighbor scans consider at most the most recent 5,000 eligible scored rows;
- all newly issued forecasts are frozen with v4 predictions immediately.

### Parallel evaluation and diagnostics

New diagnostics expose:

- v4 model/status/version;
- v4 trained forecast count;
- raw vs live-adjusted vs v3 vs v4 MAE and signed bias by horizon;
- v4 trained-only scorecards;
- per-hour v4 prediction, trained state, neighbor count, effective sample count,
  training days, and correction fraction;
- historical backfill audit;
- the exact v4 feature set and training policy.

New Home Assistant sensors:

- **Solar Learning v4 Status**
- **Solar Learning v4 Trained Forecasts**

The existing Solar Learning Status/Usable Days/Scored Forecasts sensors remain
unchanged.

No additional API calls, external ML services, or cloud storage are introduced.
Training data and models remain local to Home Assistant.
