# v0.1.26 — Dashboard legibility and reserve-floor correction

- Sunset SOC scenario ranges now use the effective reserve as their lower bound.
  This matches the planner's grid-connected operating assumption: EcoFlow should
  not discharge below the configured reserve while grid power is available.
- The four-day Battery Outlook removes the long per-column “scenario range” text
  that overlapped adjacent columns. Confidence remains visible in each day's
  header, while the sunset row shows the compact numeric range.
- Reliability states such as `learning`, `unstable`, and `unavailable` once again
  render the three-light stoplight. Yellow is illuminated with “DO NOT ACT” and
  the reason for the hold.

Grid-outage SOC behavior is outside this forecast. Supporting it later requires
an explicit grid-availability input and a separately labeled outage scenario.
