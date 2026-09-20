# v0.1.27 — Dashboard reserve-floor guard

The v0.1.26 coordinator enforces the configured reserve when generating new SOC
scenario ranges. Existing entity attributes can remain in Home Assistant until
the integration reloads and completes another forecast refresh, however.

This patch adds an independent display guard to the Battery Outlook card. It
reads `sensor.energy_planner_effective_reserve_floor`, refreshes when that entity
changes, and clamps both ends of every displayed sunset range to at least that
value. If the entity is temporarily unavailable, the display uses 10% as its
fallback floor.

The coordinator remains the source of the forecast data; this guard prevents a
stale or older lower-bound attribute from rendering below the active reserve.
