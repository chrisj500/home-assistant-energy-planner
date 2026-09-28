# Energy Planner 0.1.52

## Solar-learning history recovery

- Add an explicit `energy_planner.restore_solar_learning` action for one-time recovery of scored solar-learning evidence.
- Validate that the recovery bundle belongs to the current solar-source identity before importing anything.
- Merge only historical scored forecast outcomes; preserve live pending forecasts, live production accumulation, current source identity, and reset metadata.
- Deduplicate already-known forecast issues and reject malformed, future, or inconsistent recovery rows.
- Persist a recovery audit in solar-learning diagnostics with imported, duplicate, rejected, accepted, excluded, scorecard, and usable-day counts.
- Restrict recovery files to the Home Assistant config directory.
- Add regression tests for source mismatch, future-row rejection, deduplication, and preservation of live learner state.

This release is designed for recovery from the startup-order resets fixed in v0.1.51. Recovery data itself remains local to Home Assistant and is not stored in the repository.
