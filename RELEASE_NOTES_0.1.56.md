# Energy Planner 0.1.56

## Nest-style thermostat time-to-target

- Show a simple best-guess time-to-target directly inside the thermostat face whenever the setpoint creates a heating or cooling demand.
- Prefer the live HVAC recovery ETA once a call is underway.
- Before the HVAC call begins, estimate immediately from the current temperature gap and the learned cooling/heating recovery rate.
- Support cool, heat, and heat/cool setpoint ranges.
- Hide the estimate automatically once the target is reached or when no learned rate is available.
- Do not display confidence or sample metadata beside the estimate.
- Remove the duplicate recovery ETA from the compact HVAC learner footer.
- Keep all HVAC control behavior unchanged; this is display-only and thermostat control remains disabled.
