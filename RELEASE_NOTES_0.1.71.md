# Energy Planner 0.1.71

Adds two solar forecasting challengers in shadow mode:

- A horizon-specific blend learns conservative weights for raw, live-adjusted,
  and trained v3/v4/v4.1 forecasts from completed, matched historical targets.
- Short-term persistence scales current production along an approximate
  clear-sky horizontal irradiance curve and fades back to the provider forecast
  over two hours. It reports fallback reasons for insufficient or stale inputs.

New status and active-scored-count sensors expose the challengers. Diagnostics
save predictions, exact blend weights, evidence counts and matched scorecards.
Active-only scores distinguish useful persistence forecasts from later raw
fallbacks. Scores start prospectively; existing learning history is preserved.

Both remain observational. They do not change battery/EV control or the active
solar forecast. Persistence uses a sun-position-based horizontal proxy rather
than verified roof geometry, so its accuracy must be demonstrated locally.

No extra dependencies, subscriptions, configuration, or API requests are needed.
