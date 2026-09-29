# Energy Planner 0.1.62

## Persist authoritative EcoFlow battery topology across restarts

Energy Planner no longer falls back to manually entered battery capacity or SOC
weights while EcoFlow IoT is still starting. The last confirmed physical DPU
topology is now persisted and restored before each coordinator refresh.

- Treat live EcoFlow `bpNum` pack discovery as the authoritative physical topology.
- Persist the confirmed DPU serial mapping and per-bank pack counts in a dedicated Home Assistant storage record.
- Restore that last-known-good topology before asking EcoFlow for live topology after a Home Assistant restart.
- During temporary EcoFlow startup gaps or outages, keep the persisted pack counts/capacity and combine them only with current SOC telemetry; stale SOC is never persisted as topology.
- If no topology has ever been confirmed, wait for EcoFlow rather than fabricating capacity from configuration.
- Remove manual battery capacity, SOC-weight, and auto-topology controls from setup and options.
- Preserve existing legacy capacity keys only for one-time migration of old forecast reliability records; they are not used for live battery modeling.
- Detect a hardware topology change only by comparing a newly confirmed live topology with the persisted last-known-good topology.
- Do not invalidate daylight/overnight calibration state merely because EcoFlow initializes after Energy Planner.
- Make forecast reliability rebase only when the base coordinator reports a confirmed physical topology change.
- If the reliability store's topology signature is stale while the physical topology is unchanged, resync the signature without clearing pending evidence or creating a false rebase timestamp.
- Preserve persisted DPU bank ordering when SOC entities are late during startup.
- Remove the deprecated configured-capacity value from Battery Topology sensor attributes and expose topology-signature resync diagnostics on forecast confidence.

This fixes repeated false topology rebases such as:
`3/3/3 -> temporary manual fallback -> 3/3/3` across ordinary Home Assistant
restarts.
