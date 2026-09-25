# Energy Planner 0.1.45

## No-action counterfactual

- Adds a persistent daily counterfactual battery ledger that answers: **what would the stationary battery look like if discretionary EV charging had not happened?**
- The ledger follows observed stationary-battery movement, adds back measured solar diverted to the EV, and caps the reconstructed battery at the configured charge ceiling.
- Counterfactual energy that would have overflowed the stationary battery is recorded as avoided solar export.
- Exposes current no-action SOC/headroom, projected no-action sunset SOC/export, EV solar energy today, preserved battery headroom, and avoided export.

## Live solar capture

- Adds a forecast-independent live capture path for reversible, no-regret EV charging.
- A live opportunity is based on measured solar surplus, counterfactual battery headroom/runway, EV availability, and ten minutes of sustained surplus sufficient to cover EV charging plus a 500 W buffer.
- An unstable or still-learning multi-day forecast no longer suppresses this measured-solar opportunity.
- Forecast-dependent actions such as pre-emptive stationary-battery discharge remain behind the existing conservative reliability gate.
- Storm safety still blocks live discretionary-load recommendations.
- The legacy EV solar-advisory toggle continues to control the automation-facing EV eligibility surface; the new live-capture opportunity is informational and remains visible even when that toggle is disabled.

## Dashboard

- **What To Do** now prioritizes live solar-capture opportunities and no-action headroom risk ahead of global forecast-hold messaging.
- Adds no-action battery state, solar sent to the EV, preserved stationary-battery headroom, and avoided export to the operating view and diagnostics.

## Tracking behavior

- The counterfactual ledger starts when v0.1.45 begins running and resets at each local day boundary.
- Long telemetry gaps are not backfilled with invented EV energy; the ledger resumes from observed battery state when telemetry returns.
