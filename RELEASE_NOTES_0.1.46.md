# Energy Planner 0.1.46

## Future saturation risk

- Separates **risk visibility** from **action authorization**.
- A day that the nominal rolling model identifies as needing headroom remains visibly flagged even when the conservative reliability gate withholds an immediate action.
- Preserves nominal dynamic-load energy on each day row when the action gate suppresses the actionable recommendation.
- Battery Outlook now marks these future days in yellow instead of rendering them as clear/unmarked.

## What To Do

- Future headroom risk now takes precedence over the generic "No EV action required" state.
- If the Lexus is already full, the dashboard explicitly says the forecast still shows battery saturation risk and recommends planning another flexible load or creating EV capacity before the high-solar period.
- "Clear" from the conservative gate now means **no authorized action**, not **no forecast risk**.

This release does not change the conservative gate for forecast-dependent battery discharge; it fixes risk classification and presentation so a 100% projected battery is not silently treated as a no-risk day.
