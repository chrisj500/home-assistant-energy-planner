# Energy Planner 0.1.47

## Forecast objective: zero export

This release changes the **forecast module**, not the live solar-surplus controller.

The point forecast remains the best estimate of expected battery SOC. Risk planning is now a separate export-defense calculation whose primary objective is to avoid exporting solar.

### Directional scenarios

- **Best estimate:** the nominal interval forecast shown in Battery Outlook.
- **Energy-security bound:** lower solar / higher load. This remains useful for the lower SOC envelope and reserve visibility.
- **Export-defense bound:** higher solar / lower load. This now drives headroom-risk detection.

The previous forecast logic used the energy-security case to decide headroom risk and then subtracted an additional safety margin. That could hide exactly the high-solar days that matter most for a zero-export system.

### Export-defense headroom

- Risk is now based on the larger of:
  - simulated headroom/export shortfall in the nominal or export-defense case, and
  - the headroom required to keep the projected ending SOC outside the observed forecast-error band below the configured charge ceiling.
- A nominal 100% sunset forecast is therefore a risk even if the simulator happens to land exactly on 100% with zero modeled export.
- Forecast uncertainty increases the headroom target instead of suppressing the risk.
- No low-solar "safety margin" is subtracted from the headroom requirement.
- Today's error allowance still contracts as daylight is observed.

### Risk versus action

- New forecast-risk entities remain populated independently of the reliability/action gate:
  - Forecast Export Risk
  - Forecast Export Risk Date
  - Forecast Export-Defense Headroom
  - Forecast Flexible-Load Energy Needed
  - Forecast Export Risk Reason
- Reliability still controls whether a forecast-dependent action is authorized; it no longer determines whether the export risk exists or is shown.

### EV planning

- Forecast EV-window selection now uses the export-defense solar/load assumptions.
- Future risk days can produce a planned solar-rich EV window before that window begins.
- Immediate charging still requires the existing live measured-surplus verification.

### Dashboard

- Battery Outlook risk markers now come directly from the export-defense forecast, even when confidence/status is low.
- What To Do uses the export-risk date/headroom from the new forecast module rather than the older rolling-risk surface.
- User-facing forecast wording now refers to zero-export and export-defense behavior rather than generic "conservative" assumptions.
