# v0.1.80 — Select solar models by forecast horizon

- Route forecasts to qualified persistence under two hours and V4 from two to 24 hours only after a matched prospective accuracy win without worsening underforecast bias.
- Add a direct, provider-energy-independent measured-AC model for 24–96 hours. It uses weather, target sun geometry, hour, lead and season features, and becomes eligible only after minimum training and prospective horizon-score gates pass.
- Extend frozen solar predictions and scorecards to 96 hours, and expose independent-profile status and trained forecast count.
- Keep V3, V4.1 and the adaptive blend available for comparison while excluding them from the planning route.
- Retain the provider curve as an explicit warm-up fallback where no trained and validated model is available.

Validation uses prospective matched targets; synthetic tests verify model routing, future-data isolation, training gates and underforecast-bias checks. This release does not claim realized household savings.
