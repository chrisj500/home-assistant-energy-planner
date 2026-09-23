# v0.1.32 — Battery-first dashboard hierarchy and clearer forecast holds

- Keeps the Battery Bank card as the first content card under **Headroom Decision**,
  matching the preferred dashboard layout.
- Moves **Solar & Battery Outlook** directly below Battery Bank in the same
  section instead of leaving it as a separate lower dashboard section.
- Simplifies Forecast Guard so it shows the learning counters, storm safety
  result, and only the remaining evidence needed. The configured storm entity
  remains available in the diagnostic entity rather than being repeated in the
  primary decision card.
- Stops repeating global forecast reliability states such as `learning` under
  every day in the four-day Battery Outlook. Day headers now reserve status
  labels for verified per-day conditions.
- While reliability is still learning, **What To Do** now says
  **FORECAST LEARNING — NO RECOMMENDATION YET** instead of incorrectly saying
  **NO EV ACTION REQUIRED**.
- Other forecast hold states likewise show **FORECAST HOLD — NO RECOMMENDATION**
  until reliability is ready/clear.
- Dashboard version is now **v15**.
- Adds dashboard regression coverage for section hierarchy, global-learning
  suppression in day headers, and decision wording.

## HVAC learning

The HVAC learner remains opt-in. In Home Assistant open:

**Settings → Devices & services → Integrations → Energy Planner → Configure**

Enable **Enable read-only HVAC learning and safety holds**, verify the HVAC
entity mappings and room prefixes, and submit. The integration applies option
changes immediately; a Home Assistant restart is not required. Clean baseline
observations are retained at most once every five minutes and readiness remains
36 clean samples across at least 3 days.
