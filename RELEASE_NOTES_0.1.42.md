# v0.1.42 — correct solar learning score counts

- Score the latest issued forecast once per target hour in each lead horizon; expose issued-row counts separately.
- Measure forecast lead to the start of the target hour and migrate retained labels to the corrected horizon buckets.
- Accept Enphase lifetime production in MWh and allow a longer update interval for its cumulative meter.
- Timestamp future source-change resets; older reset records remain explicitly undated.
- Keep the learner shadow-only; no change to planner decisions or new API calls.
