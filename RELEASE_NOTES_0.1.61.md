# Energy Planner 0.1.61

## Separate time-to-target learning from precision-rate learning

A valid target-reached HVAC cycle is now retained even when the HomePod precision signal does not move far enough to produce a trustworthy °C/hour rate sample.

- Record every valid target-ended recovery as a time-to-target observation.
- Store the starting thermostat error and logical recovery duration independently of the high-resolution movement used for rate learning.
- Keep the existing 0.15 °C minimum precision-movement threshold for deriving a physical recovery-rate sample.
- Mark rate samples as valid/invalid with an explicit rejection reason instead of discarding the whole recovery cycle.
- Learn ETA directly from clean target-ended duration observations, scaled by the current thermostat gap.
- Prefer clean duration-based ETA history when available; live high-resolution rate can still materially correct the target-time countdown after enough observed progress.
- Expose separate duration-sample, rate-sample, usable-ETA, matched-duration, and matched-rate counts in diagnostics.
- Preserve the median physical recovery-rate model only from samples that actually pass the precision-rate quality checks.
- Backfill v0.1.60 post-target overruns into retained target-duration observations when possible.
- A backfilled v0.1.60 observation whose original starting gap is no longer recoverable is retained for audit/history but does not displace usable legacy ETA history.
- Persist enough recovery metadata into completed overruns so future restart/migration paths keep the target-duration evidence.

This specifically fixes cycles where the thermostat reaches the requested setpoint but the HomePod median moved less than 0.15 °C during the observed recovery.
