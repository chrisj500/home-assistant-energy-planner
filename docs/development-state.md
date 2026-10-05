# Development handoff — export-defense recommendations, 0.1.74

## Current work

Version 0.1.73 changes export-defense advice when forecast revisions are
unstable. Fresh, storm-clear forecasts with at least three horizon-matched
accuracy samples can still publish a bounded headroom recommendation when the
high-solar export-defense scenario shows a shortfall. A future overnight
discharge advisory is retained only when the base planner already considered it
safe; the amount is capped at the smaller of modeled need, 10% of bank capacity,
and stored energy above the configured reserve. The calibration now adds the
positive historical solar underprediction tail to the headroom need (capped at
25%), never discounts the need for past solar overestimates, and assumes only
the lower measured bound of natural overnight depletion. The flag remains
advisory and does not issue a battery command. Stale forecasts, storm
conditions, and insufficient accuracy evidence still block the unstable-case
exception.

Version 0.1.74 extends bounded recommendations to fresh low-confidence
forecasts when at least three horizon-matched records still identify export
risk. It also separates future EV load planning from the legacy EV solar
advisory toggle: a forecast window remains visible when that toggle is off or
the vehicle is away, while immediate automation eligibility remains disabled
until presence, advisory settings, and sustained live surplus are verified.

Future EV solar windows no longer require the vehicle to be home when the
forecast is created. A same-day window begins at least 15 minutes ahead if the
vehicle is away or presence is unknown. Any immediate charge eligibility still
requires confirmed home status and ten minutes of measured surplus; planned
windows explicitly ask the automation to recheck presence and live surplus.

Regression coverage includes calibrated export risk during provider instability,
reserve and 10% capacity bounds for overnight advice, and a same-day solar
window forecast while the EV is away or the advisory toggle is disabled. The full
316-test suite and public-repo
safety check pass.

## Previous behavior

### Battery discharge estimate, 0.1.72

Base: main at `a7f2929`, version 0.1.71. The Battery Bank dashboard card adds an
approximate time-to-configured-backup-reserve estimate while the bank is
discharging. It uses weighted whole-bank SOC, effective battery capacity, the
EcoFlow backup reserve setting, measured AC discharge power and a 90% conversion
factor. The display is advisory; it does not change reserve settings or control.

If the required SOC, capacity or reserve input is missing, the card reports the
estimate unavailable. At or below reserve it reports that reserve has been
reached. The counter updates on battery flow, SOC, capacity and reserve changes.
The 0.1.71 horizon blend and persistence challenger remain shadow-only, as
documented in `docs/solar-learning.md`.

The battery dashboard is a separate file and must be imported into Home
Assistant separately from the HACS integration.

### Validation

- Dashboard YAML parses successfully.
- Dashboard tests exercise a calculated discharge estimate, the reserve-reached
  state and missing reserve input.
- Integration control code and persisted model data are unchanged.

### Next steps

Review the pull request and its GitHub validation. After the dashboard YAML is
installed, compare the displayed estimate against subsequent SOC and discharge
power changes. Treat it as a live-rate estimate; actual runtime will vary with
load and conversion losses. Continue collecting prospective shadow scores for
the 0.1.71 blend and persistence challengers before considering promotion.
