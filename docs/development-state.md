# Development handoff — battery discharge estimate, 0.1.72

## Scope and behavior

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

## Validation

- Dashboard YAML parses successfully.
- Dashboard tests exercise a calculated discharge estimate, the reserve-reached
  state and missing reserve input.
- Integration control code and persisted model data are unchanged.

## Next steps

Review the pull request and its GitHub validation. After the dashboard YAML is
installed, compare the displayed estimate against subsequent SOC and discharge
power changes. Treat it as a live-rate estimate; actual runtime will vary with
load and conversion losses. Continue collecting prospective shadow scores for
the 0.1.71 blend and persistence challengers before considering promotion.
