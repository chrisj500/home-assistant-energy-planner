# Energy Planner 0.1.51

## Solar learning restart safety

- Preserve accumulated solar-learning history when Home Assistant starts Forecast.Solar before Enphase has registered its lifetime-energy sensor.
- Defer source-identity comparison while any required identity component is temporarily unavailable instead of treating the partial startup state as a source change.
- Persist per-component source fingerprints so future genuine source changes can identify which component changed without storing raw provider geometry.
- Expose source-identity readiness and missing components in solar-learning diagnostics.
- Add regression coverage for the exact Forecast.Solar-before-Enphase restart sequence that previously erased the model.

This release prevents Home Assistant restart ordering from resetting the AC solar shadow learner.
