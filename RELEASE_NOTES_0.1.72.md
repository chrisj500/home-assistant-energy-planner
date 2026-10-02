# Energy Planner 0.1.72

The Battery Bank dashboard card now estimates how long the bank can continue
discharging at its current measured output before reaching the EcoFlow
Smart Home Panel 2's configured backup reserve. It uses the planner's weighted
whole-bank SOC, effective battery capacity, the reserve setting, and live AC
discharge power. The estimate is approximate and refreshes as those inputs
change. If an input is unavailable, the reserve has been reached, or the bank
is not discharging, the card shows an appropriate status instead.

This is display-only and does not change battery control or reserve settings.
The Energy Planning dashboard is installed separately from the HACS integration.
