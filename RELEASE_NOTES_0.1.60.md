# Energy Planner 0.1.60

## Separate HVAC recovery from post-target overrun

Recovery learning now ends when the thermostat first reaches the requested target. If the HVAC equipment continues running afterward, that runtime is tracked as a separate control-overrun phase rather than contaminating time-to-target learning.

- Complete a recovery cycle at the first target-reached observation while the equipment is still running.
- Never train a time-to-target recovery cycle merely because the equipment stopped; a shutdown before target is recorded as an aborted recovery instead.
- Start a post-target overrun phase at target attainment and keep it active until the equipment actually stops, changes direction, or the target changes.
- Keep a physical equipment-call clock that spans both recovery and overrun phases.
- Track current and peak thermostat overshoot plus high-resolution HomePod overshoot during an active overrun.
- Learn median overrun duration and overshoot separately for cooling and heating.
- Persist and resume active overrun state across Home Assistant restarts, including repeated brief restarts.
- Keep the original equipment-call start time across restart so a 1-minute HA reboot cannot make a 50-minute HVAC call look 20 minutes old.
- Prefer newly collected clean target-ended recovery cycles over legacy stop-ended recovery history as soon as clean evidence exists; confidence still reflects the number of clean samples.
- Preserve existing restart-safe rate rebasing: long telemetry gaps can keep logical call continuity without inventing unobserved thermal response.
- Expose clean/legacy recovery population, aborted recovery reasons, physical call duration, target-reached timestamp, overrun duration, overshoot, and overrun models in diagnostics.

Thermostat control remains disabled. This release changes learning semantics, persistence, and diagnostics only.
