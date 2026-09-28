# Energy Planner 0.1.55

## Consumer-style HVAC learning

The HVAC learner now estimates early and communicates confidence instead of withholding useful estimates until a high-confidence threshold is met.

- A single completed HVAC recovery cycle can seed a provisional recovery-rate / time-to-temperature estimate.
- Recovery confidence progresses from low (1 cycle) to medium (2) to high/ready (3+).
- Expose learned cooling and heating recovery models even while HVAC is idle, including sample count, median recovery rate, confidence, and readiness.
- A single accepted passive thermal-loss window now remains usable after it closes instead of being hidden until three completed windows exist.
- Passive thermal confidence progresses from low (1 accepted window) to medium (2) to high/ready (3+).
- Preserve the existing live-window provisional estimate behavior.
- Keep thermostat control disabled and all HVAC forecasting/advice shadow-only.
- Keep completed-history persistence and the v0.1.54 restart-resilience protections unchanged.

This separates estimation from automation: low-confidence evidence can produce a visible estimate, while stronger evidence is still required before the model is labeled ready.
