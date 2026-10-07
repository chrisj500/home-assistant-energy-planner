## Energy Planner 0.1.75

- Add one export-first four-day plan with continuous battery accounting, reserve,
  power and efficiency limits, feasible EV allocations, and remaining load opportunities.
- Use persistence near-term and the learned horizon blend through 24 hours;
  keep forecasting with live/provider baselines when training is unavailable.
- Remove revision stability, confidence and solar-share vetoes from planning.
- Conserve for demonstrated multi-day low-solar reserve pressure; resume battery
  use when solar returns. Retain separate storm protection and immediate live checks.
- Show all available days before sunrise, even while the EV is away. Never reuse
  current EV charge capacity on later days or describe daily energy as battery space.
- Add dashboards/energy-planning-four-day.yaml. Import this dashboard separately
  after installing the integration to replace the old forecast and action cards.

Validation examples and reproduction instructions: docs/planning-acceptance.md.
