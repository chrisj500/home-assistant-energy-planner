# Energy Planner 0.1.49

## Automatic EcoFlow battery topology discovery

Energy Planner can now discover the physical Delta Pro Ultra battery bank from the installed **EcoFlow IoT** integration instead of requiring total capacity and per-stack weights to be maintained manually.

### How it works

- On every planner refresh, Energy Planner queries the already-refreshed EcoFlow IoT coordinator cache.
- For each Delta Pro Ultra, it reads the device-reported battery-pack count (`bpNum`) and SOC.
- Each Delta Pro Ultra battery pack is modeled as **6.144 kWh**.
- The three DPU devices are matched to Smart Home Panel 2 AC1/AC2/AC3 using their SOCs, with the previously confirmed mapping used to stabilize ties.
- Effective total capacity and individual bank capacities are rebuilt from the live pack counts.
- The existing Smart Home Panel AC-bank SOC entities remain the preferred SOC source once the DPU-to-AC mapping is known.

For example, a bank changing from `3,2,3` packs to `3,2,4` changes effective capacity automatically from **49.152 kWh to 55.296 kWh** on the next refresh.

The EcoFlow IoT `Battery pack count` Home Assistant sensor does **not** need to be enabled. Energy Planner reads the same value directly from EcoFlow IoT's coordinator quota cache, so no extra EcoFlow API call or credential is required.

### Safety and fallback behavior

- Automatic topology discovery is enabled by default.
- Existing manual `capacity_kwh` and `soc_weights` remain as a fallback and can still be forced by disabling **Automatic battery topology discovery**.
- If EcoFlow telemetry is temporarily incomplete after a topology has been confirmed, Energy Planner keeps the last confirmed pack counts/capacity rather than falling back to an older manual size.
- If an EcoFlow account contains additional DPU devices not attached to this Smart Home Panel, SOC matching selects the three that correspond to AC1/AC2/AC3.
- A physical topology change invalidates in-progress daylight/overnight calibration samples so adding a battery is not misclassified as charged energy.
- Sunset-SOC forecast-error evidence learned against the old physical capacity is reset because those percentage errors are not directly comparable after the bank size changes.

### Planner coverage

The resolved capacity now feeds the baseline planner, live sunset projection, Forecast.Solar shadow, rolling multi-day Battery Outlook, export-defense scenarios, next-sunset dashboard model, and no-action counterfactual ledger.

New entities:

- **Effective Battery Capacity**
- **Battery Pack Count**
- **Battery Topology Source**

The topology-source entity exposes effective/configured capacity, per-bank pack counts and capacities, source/fallback reason, and discovered DPU count.

Dashboard v25 shows pack count and effective capacity directly on the Battery Bank card and includes the topology entities in Diagnostics.

### Controller

The EcoFlow Solar Surplus controller is unchanged. It controls live surplus using grid/solar power, three AC-bank SOCs, charge limit and live charge capability; total stored-energy capacity is not part of that fast feedback loop.
