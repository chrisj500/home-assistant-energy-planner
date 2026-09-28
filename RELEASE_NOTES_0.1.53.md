# Energy Planner 0.1.53

## Earlier solar shadow evaluation

- Reduce the per-bucket solar-learning activation gate from 8 samples across 7 days to 3 samples across 3 days.
- Keep the learner shadow-only; it still cannot change operational planning decisions.
- Keep median-residual learning strongly regularized toward zero with the existing 20-sample prior.
- Keep learned corrections bounded to +/-25% of the provider baseline.
- Bump the shadow model diagnostic label to `hour_lead_cloud_residual_v3` so evaluations can distinguish the relaxed gate from prior runs.
- Add regression coverage proving two days remain untrained while three matching days enable shadow evaluation.

With the restored September 23-27 evidence, roughly 35 hour/lead/cloud buckets already satisfy the new 3-day gate and can begin producing learned shadow forecasts on subsequent forecast issues.
