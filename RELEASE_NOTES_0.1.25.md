# v0.1.25 — Forecast reliability and conservative EV advice

This revision prevents the rolling point forecast from immediately becoming a
headroom or EV charging instruction.

## Ranges and confidence

Each modeled day exposes a sunset SOC scenario envelope. Physical stress cases
use 30% less solar with 30% more load and greater overnight depletion, and the
reverse case. Ranges also include observed absolute errors for that lead day,
with an initial floor of 10 SOC percentage points plus 3 points per day ahead.
These are engineering scenarios, not probabilistic confidence intervals.

Confidence is `learning` until three scored predictions exist for the relevant
lead day and three overnight observations exist. `medium` requires mean absolute
sunset SOC error at most 10 points; `high` requires ten predictions and error at
most 5 points. Otherwise confidence is `low`. Errors are end-to-end outcomes and
can include changed household behavior. Each day has its own evidence; headline
confidence uses the candidate risk day or the first forecast day if none exists.

One fixed forecast is stored per issue date and target date, at least six hours
before target sunset. It is scored against actual SOC only within five minutes
after sunset. Missed observations are discarded. Evidence starts with this
revision; old baseline calibration is not rolling-model validation.

## Decision rules

- Compare like target dates over three hours. A sunset SOC swing over 10 points,
  or future daily solar swing over both 5 kWh and 25%, marks forecasts unstable.
  Today's naturally shrinking remaining solar is not treated as a revision.
- Conservative capacity shortfall must exceed the largest of 2 kWh, 5% of bank
  capacity, or the historical/error-floor SOC allowance converted to kWh.
- Only risk within two days may authorize advice. The same risk date must survive
  three successful provider refreshes over at least an hour. Cached minute
  updates do not count. Reversal or restart requires new confirmation.
- EV advice requires enabled solar advisory, known home status, fresh EV SOC,
  a conservative window starting now with at least 90% forecast solar supply,
  and ten minutes of fresh measured surplus covering EV power plus 500 W.
  Gaps, stale readings, or loss of surplus revoke eligibility. The configured
  base-load sensor must measure non-EV house power; verify its mapping.
- Missing/stale data, storm protection (including unknown state), low confidence,
  and instability withhold action across legacy strategies, EV prompts, dynamic
  load flags, and headroom-release outputs.
- **Automatic stationary-battery headroom release is disabled in this revision,
  even if its existing option is enabled.** Only verified EV advice is permitted;
  a validated discharge controller is not part of this change.

Forecast records, revision observations, and the last 100 decision/input snapshots
persist in Home Assistant storage. Histories are bounded and are not backfilled.
Original point predictions remain available for diagnostics.

## Installation and dashboard

Update the integration once this version is released and restart Home Assistant.
This source change does not install itself. Update the supplied
`dashboards/energy-planning.yaml` separately; manually installed dashboards are
not automatically replaced.

New entities: Forecast Confidence, Forecast Reliability Status, Forecast
Reliability Reason, and Forecast Error Samples. Confidence/status attributes
include per-day ranges, error counts, historical error, and refresh confirmations.
The dashboard displays hold warnings instead of green "no action needed" when
unverified, and sunset SOC ranges by day.

## Validation limits

Tests cover the reported 28.4 → 75.5 → 22.1 kWh solar swing, cached updates,
reversals, missing/stale telemetry, cold start, physical scenario simulation,
fixed-snapshot scoring, missed sunset, and measured-surplus qualification and
revocation. This is a synthetic regression using reported values, not a full
chronological replay of September 18.

Coordinator tests use real methods with Home Assistant I/O faked. Live installation
has not been validated. Thermostat demand, HVAC recovery modeling, and thermostat
control are not implemented. Those require the telemetry discussed separately.
A stable forecast can still be wrong; these checks do not establish real-world
accuracy. Engineering thresholds need evaluation on subsequently collected data.
