# Energy Planner 0.1.43

## Battery Outlook

- Replaces the wide scenario-envelope headline with a single live best-guess sunset SOC.
- Shows empirical sunset SOC error as a compact `±N%` value when scored history exists, plus the current confidence label.
- Keeps the conservative low/high scenario envelope in the day-plan attributes for diagnostics and safety decisions.

## Live current-day forecast

- Re-simulates the nominal rolling horizon from the current battery SOC using the live-anchored solar interval curve.
- Current-day display estimates therefore respond to measured solar power instead of remaining fixed to the provider curve.
- Future days continue from the newly simulated battery state while retaining the provider interval forecast.
- Operational headroom and EV actions remain governed by the conservative scenario envelope and existing reliability gate.
