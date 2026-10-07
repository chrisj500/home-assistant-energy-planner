# Four-day export-first planner — 0.1.75

## Current implementation

The final coordinator is now EnergyPlannerPlanningCoordinator, which publishes
one authoritative four-day plan after legacy telemetry and learner updates.
Its independent 15-minute per-bank ledger carries storage across all four days,
serves household demand from the bank down to reserve, applies physical power
and efficiency limits, and allocates current EV capacity once. Future EV return
opportunities are separate, bounded by known full-range capacity and available
window power; they are not counted as scheduled loads or avoided export.

Persistence is evaluated from fresh production for the next two hours. The
existing horizon-weighted blend is evaluated through 24 hours, using completed
prior outcomes, with raw provider intervals beyond 24 hours. Missing training
uses the provider/live baseline, not a hold. Forecast changes recompute the plan;
legacy instability, refresh streaks, and historical confidence do not veto it.

Conservation requires two complete low-solar days that reach reserve even in a
30%-higher-solar simulation. It ends when the low-solar run ends. Storm protection
retains stored energy. Missing storm status is explicitly reported. Absent later
forecast days are labelled missing while covered days continue to be planned.
Charging limits come from the installed controller. The discharge planning limit
uses that verified charge-power ceiling as a conservative assumed discharge
ceiling, explicitly labelled in diagnostics; it is not a measured discharge limit.

## User interface and installation

Install the integration, restart Home Assistant, and replace the old dashboard
with dashboards/energy-planning-four-day.yaml. This keeps the existing live
battery, HVAC and EV controls, while the three forecast/action cards read only
sensor.energy_planner_four_day_plan. The legacy dashboard remains for compatibility.
The new view shows expected solar, house energy, EV allocations, remaining export,
battery use before sunrise, and windows for other useful loads on every day.
No generic flexible loads are invented or counted as scheduled.

## Validation and evidence

See docs/planning-acceptance.md and run python scripts/planning_acceptance.py.
Those results are synthetic fixtures, not claimed realized household savings.
Tests exercise the real final coordinator, publication, reserve and energy
conservation per interval, four risk days, pre-sunrise planning, an absent EV,
partial-solar EV windows, revisions, sustained low solar, sunny recovery, missing
later coverage, and agreement between authoritative display outputs.

The October 6 diagnostic does not contain the original multi-day provider curve;
a full replay of that exact live forecast is not claimed. Live outcome validation
requires the installed build and subsequent diagnostics. No Home Assistant
installation or device action is performed by this repository change.

## Previous implementation history

# Unreleased correction — forecast opportunities and physical quantities

The October 7 dashboard audit found a same-day EV search ending before sunrise,
a 90% solar-share filter suppressing useful mixed-supply forecasts, a single-day
summary overwriting multi-day risk, and stress-case daily energy labelled as
stationary battery headroom. Earlier fixes did not test these user outcomes.

Local corrections search the remaining daylight (while retaining an immediately
useful live window), allow partial-solar and partial-duration forecasts, report
all risk days even during learning, and use nominal solar/load for expected EV
energy contributions. Immediate charge eligibility remains a separate live
verification. The overnight advice no longer has an arbitrary 10% capacity cap;
it remains bounded by the base planner's computed release, modeled need, and
stored energy above reserve. Daily rows expose nominal capacity export, stress
capacity export, and battery energy above reserve separately. The dashboard
labels the stress quantity as a daily opportunity, not achievable headroom.

Validation: 319 regression tests pass, including pre-sunrise planning,
50% solar/50% grid window retention, and four visible risk days while learning.
Dashboard YAML parses. These checks establish the specific corrections only.
Outstanding: recorded end-to-end replays and realized avoidable-export outcomes;
reconcile the differing legacy/counterfactual outlook totals; integrate the
proposed horizon-selected solar forecasts (currently still shadow-only).
Do not claim the project's operational objective is validated or deployed.
Dashboard changes require a separate dashboard import.

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
