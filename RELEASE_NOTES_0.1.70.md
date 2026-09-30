# Energy Planner 0.1.70

## Add Toyota remote controls to the EV card

The EV card now shows door-lock status and provides Toyota NA lock, unlock,
remote start, remote stop, and refresh actions. Remote start and stop require a
confirmation. The controls resolve the Toyota device from the configured EV
SOC entity and stay hidden when that device cannot be identified.

### Validation

- EV card tests for lock status, service actions, and start/stop confirmation
- Dashboard YAML validation
