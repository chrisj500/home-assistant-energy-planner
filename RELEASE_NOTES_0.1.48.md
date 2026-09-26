# Energy Planner 0.1.48

## Fix false export-risk warnings after forecast/weather changes

v0.1.47 corrected the forecast objective to zero export, but its export-defense uncertainty could still overreact in two ways:

1. It added historical forecast error on top of an already stressed high-solar/low-load scenario.
2. It carried the stressed scenario forward across consecutive days, allowing hypothetical prior high-solar days to inflate later-day starting SOC.

This release fixes both.

### Per-day export defense

- The nominal rolling forecast remains sequential and is still the best estimate.
- Each day's export-defense and energy-security stress test now starts from **that day's nominal battery state**.
- High-solar/low-load stress is no longer compounded across every prior day in the horizon.
- This prevents a worsening weather forecast from leaving multiple future days falsely marked as saturated simply because earlier hypothetical stress days were allowed to accumulate.

### Directional forecast error

- Historical sunset error is now retained with its sign.
- `error_soc = actual - predicted`: only a positive systematic bias means the forecast has tended to under-predict ending SOC and therefore creates unexpected-export risk.
- Historical over-prediction no longer creates battery headroom.
- The display envelope remains two-sided; this change affects the zero-export risk calculation only.
- The historical directional bias is applied to the **nominal** forecast, not added on top of the already stressed export-defense case.

### Expected behavior

A day like the 2026-09-26 diagnostic snapshot—22% SOC, ~54% nominal sunset SOC, ~79% high-solar stress sunset SOC, and zero modeled export—will no longer be flagged solely because a broad historical error band overlaps the charge ceiling.

Direct high-solar capacity/export shortfall still creates an export-risk warning, and a nominal forecast near 100% can still be flagged when the planner has a demonstrated positive under-prediction bias.
