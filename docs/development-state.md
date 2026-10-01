# Development handoff — solar challengers, 0.1.71

## Scope and behavior

Base: main at `4c59e86`, version 0.1.70. This change adds the requested
horizon-specific blend and short-term persistence challenger, both shadow-only.
No equipment control, active forecast selection, dashboard layout, or existing
v3/v4/v4.1 historical predictions are changed.

The blend uses completed matched target-hour outcomes in each horizon, a recent
48-target/21-day window, a 12-target/3-day gate and baseline-shrunk inverse-MAE
weights. The persistence challenger uses current AC production, a Haurwitz GHI
shape proxy and a two-hour linear fade to the raw forecast. It does not model
roof orientation or inverter clipping. See solar-learning.md for exact policies.

Predictions and metadata freeze at issuance and persist with existing HA storage.
Challenger scores are prospective-only. Active-only cohorts distinguish actual
model use from fallback. Existing hourly scoring does not cover the current
partial hour or 15-minute windows. No automatic promotion is implemented.

## Validation

- 311 unittest regressions passed locally.
- Public-repository safety check and whitespace check passed.
- The blend was exercised on ten pending forecast rows from a private diagnostic:
  finite nonnegative predictions and normalized weights, about 0.005 seconds.
  This is a functional check, not evidence of forecast accuracy.
- Tests cover matched cohorts, temporal isolation, duplicate target evidence,
  fallback inputs, immutable forecasts, storage restoration and control isolation.
- Private diagnostics and site coordinates remain outside repository artifacts.

## Next steps

Review the pull request and its GitHub validation. Merging main triggers the
repository's release workflow. After installing 0.1.71, allow the next hourly
issuance and target resolution, then inspect blend/persistence diagnostic
scorecards and active scored counts. Keep models shadow-only while gathering
multiple days and weather regimes. Persistence is not yet a blend member.
