# v0.1.36 — Battery Outlook resilience and truthful diagnostics

This release fixes two failure modes that could make Battery Outlook disappear
while other solar totals still looked healthy.

## Forecast.Solar interval resilience

Battery Outlook uses the detailed Forecast.Solar interval curve, which is
different from the aggregate Forecast.Solar entities that can still show daily
or horizon energy totals.

Previously:

- the interval payload lived only in memory, so every Home Assistant or
  integration restart started with no interval curve;
- any transient Forecast.Solar refresh failure (including rate limiting,
  timeout, or provider error) cleared the last good interval payload;
- Battery Outlook then lost all modeled days even though aggregate solar
  forecast data remained visible.

v0.1.36 now:

- persists the last successful interval payload and its success timestamp in
  Home Assistant storage;
- restores that payload after restart when it matches the current Forecast.Solar
  site geometry;
- keeps the last good interval curve through transient refresh failures instead
  of erasing it;
- still uses the existing reliability freshness gate before any discretionary
  recommendation can become actionable;
- exposes whether the current interval curve came from live data or the restored
  cache, along with the last-success timestamp and latest refresh error.

A rejected API key (401/403) remains fail-closed.

## Battery SOC validity

The reliability layer no longer rejects a numeric battery SOC simply because
Home Assistant has not re-reported an unchanged value within 15 minutes.
A numeric available SOC remains valid until Home Assistant marks the entity
unknown/unavailable or its state stops being numeric.

## Battery Outlook diagnostics

The rolling battery model now records a specific status/reason when it cannot
build the horizon, including:

- interval solar forecast unavailable;
- planning base load unavailable;
- one or more battery SOC inputs unavailable;
- battery charge-limit input unavailable/non-numeric;
- missing solar daylight windows;
- simulation produced no modeled days.

Dashboard v18 shows that reason directly instead of four columns of unexplained
dashes.

## Forecast-learning continuity

A battery-model outage no longer renders persistent learning evidence as 0/3.
Stored scored-sunset and overnight-calibration counts remain visible even while
Battery Outlook is unavailable.

No existing forecast-learning, HVAC-learning, recovery, thermal, or calibration
history is reset by this release.
