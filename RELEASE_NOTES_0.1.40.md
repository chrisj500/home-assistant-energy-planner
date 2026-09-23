# v0.1.40 — stop midday solar inflation

- Remove local-total rescaling from rolling, reliability, and paid shadow forecasts.
- Blend live power into the next hour without compensating later in the day.
- Own solar policy in the integration, with automatic migration of the known legacy
  YAML remaining-energy selection to the raw Forecast.Solar fallback.
- Document retirement of old YAML forecasts and clarify the fallback option label.
- Preserve HVAC learning and existing battery/reliability history.
