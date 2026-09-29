# Energy Planner 0.1.67

## Add tuned solar v4.1 shadow learner

v0.1.66 introduced the first continuous weather-aware v4 solar residual model.
Its initial shadow evaluation proved that continuous cloud neighborhoods solve the
categorical sparsity problem, but also showed that medium- and long-horizon
corrections could become too aggressive.

v0.1.67 keeps v3 and the original v4 frozen as controls and adds a separate
`continuous_weather_residual_v4_1` challenger. All three remain shadow-only;
no learned solar result changes the operational Forecast.Solar curve, battery
strategy, EV advice, or equipment controls.

### Tighter lead-time locality

v4.1 rejects historical neighbors whose original forecast lead differs too much
from the candidate:

- 0–3h target: +/-1.5h
- 3–12h target: +/-3h
- 12–24h target: +/-4h

Lead time is then weighted strongly inside that hard window.

### Target-hour solar geometry

Every v4.1 forecast row freezes approximate target-midpoint solar elevation and
azimuth derived locally from the Home Assistant site location and target
timestamp. The site coordinates themselves are not added to forecast rows or
diagnostics.

Solar geometry receives more weight than wall-clock hour, allowing comparable sun
positions to inform one another as day length and solar position change through
the season.

### Evidence- and performance-scaled correction authority

v4.1 retains the same early evidence gate as v4: at least six unique target hours
across three days with at least three effective weighted neighbors.

After that gate, correction authority grows gradually instead of immediately
allowing a large residual:

- early neighborhoods are regularized more strongly toward Forecast.Solar;
- the evidence cap begins at 2.5 percentage points plus 0.6 points per effective
  sample, with a hard +/-20% mature ceiling;
- represented-day count further limits early authority;
- before four prior trained v4.1 outcomes exist in a horizon, only 25% of earned
  authority is available;
- with four to seven prior trained outcomes, provisional authority is 35%;
- at eight or more, v4.1 compares its own prior frozen MAE with raw
  Forecast.Solar over up to the most recent 24 targets in that horizon;
- if v4.1 has been worse than raw, correction authority is reduced by the cube of
  the raw/v4.1 MAE ratio, with a 15% floor;
- if v4.1 is beating raw, it may earn the full evidence-limited authority.

Longer weather horizons also gain authority more slowly:

- 0–3h: 100% of otherwise earned authority
- 3–12h: 65%
- 12–24h: 40%

### Parallel, frozen comparison

The original v4 model and its historical predictions are not rewritten. v4.1
gets its own chronological out-of-sample replay using the same anti-leakage rule:
only target outcomes completed before the original prediction issuance may train
that prediction.

Diagnostics now compare:

- raw Forecast.Solar
- live-adjusted forecast
- v3 categorical residual
- original v4 continuous residual
- tuned v4.1 continuous residual

for the same resolved target hours, with separate trained-only scorecards.

Per-row v4.1 diagnostics include target solar geometry, neighbor/effective sample
counts, lead window, prior performance sample count, performance scale, correction
cap, final correction, and horizon authority.

New Home Assistant sensors:

- **Solar Learning v4.1 Status**
- **Solar Learning v4.1 Trained Forecasts**

No additional API calls, cloud ML services, or external storage are introduced.
