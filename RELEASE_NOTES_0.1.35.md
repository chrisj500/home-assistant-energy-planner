# v0.1.35 — HVAC context readability and import-cost display

Dashboard v17 refines the HVAC card based on the real Home Assistant render.

- Moves outdoor temperature context **below the thermostat dial and above the
  individual room tiles**, where it has enough horizontal space to remain
  legible.
- The temperature context now shows current outside temperature, thermostat
  target, and the signed outside-vs-target delta.
- Adds a second context line comparing **outside relative humidity** from the
  configured weather entity with **inside thermostat humidity**, including the
  signed percentage-point delta.
- Keeps the existing configured outdoor-temperature and weather entity mappings
  rather than hard-coding a specific sensor.
- Adds an HVAC electricity cost line beneath the current/today electricity
  values using the current base import rate of **$0.25/kWh**:
  `estimated cost today = HVAC daily kWh × $0.25/kWh`.
- The rate is intentionally defined once in the HVAC dashboard card so it is
  easy to replace with a dynamic tariff entity later.

No HVAC learner model or stored learning history is reset by this release.
