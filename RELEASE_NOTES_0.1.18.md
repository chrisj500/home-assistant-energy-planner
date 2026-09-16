# Energy Planner v0.1.18

## Goal

Make the dashboard answer the operational question directly: **over the next forecast horizon, which days need useful dynamic load to keep solar onsite, and how much?**

## Changes

- Preserve the v0.1.17 authoritative current-day headroom correction.
- Simulate every available Forecast.Solar day sequentially, carrying stationary-battery state across overnight depletion and the following solar day.
- Expose a new `Dynamic Load Forecast` binary sensor with a per-day plan list, risk dates, and total useful-load requirement.
- For each modeled day expose:
  - solar forecast,
  - modeled start SOC,
  - modeled sunset SOC,
  - available and required battery headroom,
  - capacity-limited export,
  - power-limited export,
  - useful dynamic-load energy needed to avoid capacity-driven export,
  - forecast source and current-day correction factor.
- Keep today's locally corrected interval curve confined to today; future days remain on the provider forecast.
- Restore a dedicated EV / Flexible Load dashboard section with Lexus SOC, charger state, learned charging power, available EV energy, recommended EV energy, and preferred charging window.
- Replace the first-risk-only dashboard summary with a visible multi-day decision table.
- Carry forward the Energy History Export button restored after v0.1.17.

## Decision semantics

`Dynamic load needed` is based on capacity-driven headroom pressure. It is the larger of:

1. modeled AC energy that would otherwise be exported because the stationary battery is full, or
2. stored-energy headroom shortfall converted back to equivalent AC energy using modeled charge efficiency.

Power-limited or control-limited export remains separate because creating more storage headroom does not necessarily solve it.

## Validation target

For each strong-solar day, compare the day-ahead/current-day forecast row with actual battery SOC and grid export. The model should correctly identify risk days and converge on the useful dynamic-load energy needed to reduce avoidable capacity-driven export toward zero without compromising the backup reserve.
