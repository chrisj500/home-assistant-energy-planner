# Energy Planner 0.1.81

Fix the four-day outlook failing with `list index out of range` during local evenings when Home Assistant supplies sunrise and sunset in UTC.

- Build each planning day from its local calendar date and Home Assistant timezone.
- Simulate elapsed time across daylight-saving transitions so 23- and 25-hour days account for the correct household energy.
- Return a clear unavailable reason when no current or future planning days remain.
- Preserve the battery reserve, charge ceilings, and existing automation eligibility checks.

Regression coverage includes western and eastern time zones, partial evenings, both daylight-saving transitions, and interval energy continuity.

This release does not infer that a 0% battery is disconnected. Bank availability and any intentional exclusion still require verified connection status.
