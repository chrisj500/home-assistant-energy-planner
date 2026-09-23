# Solar configuration

## Ownership

The forecast algorithm, 60-minute live blend setting, and migration are versioned
in this Energy Planner project. HACS updates install the policy. Entity selections
and API credentials remain in Home Assistant's integration settings; they are not
source configuration and must not be committed to this public repository.

## v0.1.40 policy

Use Forecast.Solar interval energy directly. Anchor current power to measured solar
production, tapering the difference linearly to zero within 60 minutes or sunset,
whichever comes first. Clamp power at zero. Do not compensate elsewhere in the day.
Without live power, keep the provider curve. Without interval data, use the configured
raw remaining-energy fallback. This release does not claim that the one-hour blend
has outperformed the raw forecast; keep the paid raw sensor for independent scoring.

The former YAML policy used a 30-minute median of actual/forecast power, clipped
instantaneous ratios to 0.35–4, then applied `clip(1 + 0.8*(median-1), 0.6, 3)`
to all remaining energy. This short-term extrapolation is retired.

## Upgrade

1. Install v0.1.40 or later and restart Home Assistant. Integration setup migrates
   the known legacy remaining-solar selection to
   `sensor.energy_production_today_remaining`. Custom selections are preserved;
   select a raw provider energy sensor in options if yours has a different ID.
2. No edit to configuration.yaml is needed for the planner fix. The integration
   no longer uses the local correction to scale interval forecasts.
3. Old YAML entities may still exist for other dashboards or automations. Before
   deleting them, replace their references. Retired solar correction entities are
   `sensor.solar_forecast_instant_bias`, `sensor.solar_forecast_bias_30m_median`,
   `sensor.solar_forecast_correction_factor`, and
   `sensor.solar_forecast_remaining_today`.
4. Legacy YAML discretionary-energy and sunset-SOC templates also reference that
   old corrected sensor. Retire those forecasts in favor of Energy Planner's
   integration entities; do not leave automations acting on the old advice.

Expected diagnostics: current-day scale factor 1.0; live anchor enabled when
production is available; remaining energy can rise or fall only through provider
updates or the near-term live adjustment, never a whole-day local multiplier.
