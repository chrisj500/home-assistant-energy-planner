# Energy Planner 0.1.50

## Preserve forecast learning across battery upgrades

This release supersedes the reliability-reset behavior introduced in v0.1.49.

Energy Planner now stores completed sunset forecast errors in **physical kWh** together with the battery topology/capacity that existed when the forecast was issued and scored. When the physical battery bank changes, completed reliability evidence is preserved and re-expressed against the new effective capacity instead of being discarded.

### Reliability record schema v2

Newly scored sunset forecasts retain:

- issued SOC prediction
- predicted stored energy in kWh
- actual sunset SOC
- actual stored energy in kWh
- signed forecast error in kWh
- battery capacity at issue/settlement
- issued and settled topology signatures
- lead time and issue timestamp

Existing legacy records are migrated in place. Records that predate topology metadata use the planner's previously configured capacity as the migration basis, because that was the physical capacity the old planner used to produce and score those SOC forecasts.

For an old 49.152 kWh bank, a +10 percentage-point sunset miss is stored as +4.9152 kWh. After expanding the bank to 55.296 kWh, that same physical historical miss remains +4.9152 kWh and is equivalent to about +8.9 percentage points on the larger bank.

### What is preserved

A detected battery topology change **does not clear completed forecast reliability records**.

It also continues to preserve:

- solar learner history
- HVAC learner history
- completed daylight calibration records
- completed overnight calibration records
- EV learning data

Only transient state that crosses the physical hardware change is invalidated:

- an unscored pending sunset forecast issued against the old topology
- current forecast-confirmation/revision streak
- current decision-history window
- the same-day no-action counterfactual ledger
- in-progress daylight/overnight calibration samples (from v0.1.49)

These transient items cannot be compared safely across a mid-sample hardware change.

### Topology-normalized reliability

Forecast MAE and directional export bias are now derived from the stored kWh errors and then expressed against the **current** effective battery capacity for SOC-based display and policy thresholds.

This means increasing battery capacity no longer erases learning, while the same historical energy error naturally represents a smaller SOC percentage on a larger bank.

New reliability diagnostics include:

- historical MAE in kWh
- signed historical bias in kWh
- export-underprediction bias in kWh
- reliability record schema version
- total preserved reliability record count
- migrated legacy record count
- legacy records whose capacity was inferred
- most recent battery-topology rebase time
- completed samples preserved during that rebase
- pending cross-topology samples discarded

### Upgrade note

If you have **not yet installed v0.1.49**, upgrade directly to **v0.1.50**. That preserves the reliability evidence collected on the old battery bank while also gaining v0.1.49's automatic EcoFlow battery-topology discovery.

If v0.1.49 has already run and cleared old reliability records, those deleted records cannot be reconstructed from the reliability store by v0.1.50.
