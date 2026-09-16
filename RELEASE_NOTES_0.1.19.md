# Energy Planner v0.1.19

This release is a focused dashboard usability fix following the v0.1.18 multi-day dynamic-load release.

## Changes

- Fixes the next-7-days dynamic-load forecast so Home Assistant renders the rows as an actual Markdown table instead of folding them into a single paragraph.
- Removes the brittle dashboard dependency on the missing `sensor.energy_planner_ev_charge_power` entity.
- Exposes learned EV charge power and its source as stable attributes on the existing EV Auto-Charge Eligible binary sensor.
- Renders learned EV charge power from those stable attributes in the EV / Flexible Load section.
- Keeps the Energy History Export button and the v0.1.18 multi-day decision model unchanged.

No headroom, solar, battery, or dynamic-load modeling semantics are changed in this release.
