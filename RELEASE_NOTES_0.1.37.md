# v0.1.37 — Preserve HVAC learning across Home Assistant restarts

Fixes a startup persistence bug that could erase the HVAC learner even though
its history was stored in Home Assistant storage.

## Root cause

The HVAC learner already persisted:

- clean five-minute training samples;
- completed recovery cycles;
- passive thermal samples.

However, after loading that store, the coordinator compared the saved entity
mapping with the room prefixes resolved during startup. HomePod entity aliases
can legitimately resolve between:

- `sensor.homepod_indoor_climate_...`; and
- `sensor.home_homepod_indoor_climate_...`.

If those aliases differed during Home Assistant startup, the coordinator treated
that as a new HVAC data source and replaced the entire store with a blank model.

## v0.1.37 behavior

- Legacy/current HomePod prefixes are canonicalized before comparing model
  identity, so alias-only resolution changes no longer reset learning.
- Existing learned samples, completed recovery calls, and passive thermal
  samples are retained across restart.
- Older HVAC stores without an entity-mapping marker are adopted in place
  instead of being erased.
- Only continuity-dependent state is discarded at restart:
  `previous`, active response/fault timers, an in-progress recovery call, and
  an in-progress passive thermal window.
- A genuinely different configured data source (for example a different
  condenser-power entity) still starts a fresh model rather than mixing
  incompatible historical measurements.
- HVAC Model Status diagnostics now include a `persistence` block showing
  whether history was restored or reset and how many samples/cycles were
  restored.

This release cannot reconstruct samples already erased by an older version, but
once upgraded, newly accumulated HVAC learning should survive future Home
Assistant and integration restarts.
