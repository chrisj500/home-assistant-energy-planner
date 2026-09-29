# Energy Planner 0.1.64

## Fix Home Assistant config-entry migration registration

v0.1.63 correctly defined the legacy battery-topology config cleanup, but
registered the migration coroutine on the ConfigFlow class. Home Assistant
looks for config-entry migration at the integration module level, so existing
version-1 entries logged:

`Migration handler not found for entry Energy Planner for energy_planner`

This patch:

- adds the required module-level `async_migrate_entry(hass, entry)` handler in
  `__init__.py`;
- removes the misplaced ConfigFlow migration method;
- keeps config-flow entry version and integration migration target on one shared
  `CONFIG_ENTRY_VERSION = 2` constant;
- migrates existing entries by removing only the deprecated
  `capacity_kwh`, `soc_weights`, and `auto_battery_topology` keys;
- preserves all unrelated entry data/options;
- rejects only impossible future entry versions instead of silently rewriting
  them;
- adds a regression that requires the migration handler to remain module-level
  and absent from ConfigFlow.

No battery-learning, solar-learning, HVAC-learning, or forecast logic changes in
this patch.
