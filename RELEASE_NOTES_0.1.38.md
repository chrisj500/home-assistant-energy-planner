# v0.1.38 — Compact operating dashboard and downloadable diagnostics

This release restructures Energy Planning around a single-screen operating
view and moves deep troubleshooting detail into a dedicated Diagnostics view.

## Compact operating view

Dashboard v19 keeps the four daily decision areas at the top:

1. **HVAC Overview**
2. **Battery Bank / Headroom Decision**
3. **Battery Outlook — Next 4 Days**
4. **What To Do**

The HVAC card is materially shorter without removing the information needed for
daily operation:

- smaller thermostat dial;
- compact indoor-room tiles;
- outside temperature/humidity context remains visible;
- current/today HVAC electricity and estimated cost remain visible;
- HVAC learner progress remains visible;
- detailed recovery/thermal/power-source diagnostics move to Diagnostics.

The Battery Bank remains the first card in the second column. Its former stack
of separate sunset, rolling-energy, reliability, traffic-light, and export cards
is condensed into one **Solar & Battery Outlook** summary immediately below it.

The **What To Do** column is now one compact decision card that includes Lexus
SOC, available/recommended EV energy, and the verified charge window.

The operating view contains four compact sections with no Forecast Quality,
history-export, or low-level model cards competing for vertical space.

## Diagnostics view

A new **Diagnostics** dashboard view contains:

- forecast reliability and learning evidence;
- Battery Outlook model status/reason and raw modeled inputs;
- configured battery SOC values when an outlook prerequisite fails;
- HVAC model status, power, daily energy, persistence restore/reset status,
  recovery state, and passive thermal-learning state;
- Forecast.Solar quality/calibration tiles;
- links to the Energy Planner integration and Energy History.

## Home Assistant diagnostics download

Energy Planner now implements Home Assistant's standard config-entry diagnostics
hook. From the Energy Planner integration menu, **Download diagnostics** produces
one support bundle containing:

- the config entry with the Forecast.Solar API key redacted;
- every configured entity ID and its current raw Home Assistant state,
  attributes, and timestamps;
- all current Energy Planner sensor/binary-sensor states;
- current coordinator outputs;
- Forecast.Solar interval-cache origin, last success/attempt, and refresh error;
- HVAC persistence status and stored sample/cycle counts.

This should eliminate the need to troubleshoot planner failures from multiple
screenshots or option pages. No learning/calibration history is reset by this
release.
