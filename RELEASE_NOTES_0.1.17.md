# Energy Planner v0.1.17 — Authoritative Headroom Decision

## Goal

Keep as much solar production onsite as possible without sacrificing backup resilience or creating unnecessary stationary-battery cycling.

The planner's primary decision is now whether the stationary battery will run out of useful headroom before the available solar can be absorbed. Flexible loads are recommended only when they address that physical problem.

## What changed

- The paid Forecast.Solar interval curve remains the timing/shape source.
- For the current solar day only, the interval curve is scaled to the configured locally corrected remaining-solar energy before rolling battery, export, and EV calculations run.
- Future forecast days remain unscaled so today's production bias does not contaminate tomorrow's weather forecast.
- Rolling headroom risk, EV charge-window selection, and the multi-day solar-after-house-load outlook now consume the same corrected current-day curve.
- A new `Headroom Risk Today` binary sensor provides a single decision surface with risk date, modeled storage shortfall, capacity-limited export, forecast source, scale factor, and recommended action.
- The dashboard is decision-first: headroom status is shown before forecast diagnostics or EV details.
- The old ambiguous `surplus` dashboard wording is replaced with `solar after house load`, distinguishing available solar energy from actual unavoidable export.

## Decision hierarchy

1. Solar serves the house.
2. Remaining solar charges the stationary battery within its physical power and capacity limits.
3. If storage headroom is forecast to run out, use genuinely useful flexible load such as EV charging.
4. Let normal overnight consumption recreate headroom for the next day while respecting reserve.
5. Do not intentionally cycle the stationary battery merely to manufacture headroom unless confidence-calibrated policy explicitly justifies it.
6. Storm protection and backup reserve remain higher-priority constraints.
7. Export only when capacity, charge-power, or control constraints make it unavoidable.

## Validation target

For each strong solar day, compare the planner's headroom-risk decision and capacity-export estimate with actual battery SOC and grid export. The release is successful when avoidable capacity-driven export approaches zero without unnecessary battery depletion or reserve compromise.
