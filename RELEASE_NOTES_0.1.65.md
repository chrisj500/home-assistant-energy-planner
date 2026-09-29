# Energy Planner 0.1.65

## Preserve the horizon-decayed load profile into Battery Outlook

v0.1.63 added a per-day planning-load profile that decays short-term 3-hour
load influence toward the 24-hour baseline. The profile was calculated correctly
inside the enhanced coordinator, but the normal success result omitted the
profile fields before the v0.1.18+ Battery Outlook layer re-simulated the days.

That caused diagnostics such as:

- `planning_recent_3h_w: null`
- `planning_recent_24h_w: null`
- `planning_load_profile_w: []`
- every modeled future day reusing the same scalar `planning_load_w`

This patch:

- carries the recent 3-hour input, recent 24-hour anchor, per-day load profile,
  and profile source through the normal coordinator success output;
- keeps safe empty defaults in fallback results;
- lets the downstream Battery Outlook simulation consume the intended per-day
  load profile;
- adds a regression requiring those fields to exist on both fallback and success
  paths.

No load-model coefficients changed. The intended v0.1.63 behavior is simply wired
through correctly.

The fresh v0.1.64 diagnostics also validated that config-entry migration to
version 2 succeeded and battery topology remained 3/3/3 = 55.296 kWh without a
new restart-induced topology rebase.
