# v0.1.41 — AC solar shadow learning

- Automatically collect Enphase AC production and optional Ecowitt observations.
- Archive forecasts as issued, then score them only against sufficiently covered
  actual production hours. Persist history and exclude gaps, resets, and stale inputs.
- Add a bounded, regularized residual learner by local hour, lead time, and cloud
  category. It remains in shadow mode and never changes operational decisions.
- Compare paid, live-adjusted, and learned predictions on the same frozen targets.
- Add Solar Learning status/count sensors, integration options, and diagnostic exports.
- Reuse existing paid forecast/weather requests; no new API calls or subscription.

No YAML changes required. Review Solar Learning Status attributes after updating;
select alternate Ecowitt entities in integration options if the defaults are absent.
