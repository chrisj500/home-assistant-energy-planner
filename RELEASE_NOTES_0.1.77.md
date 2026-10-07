# v0.1.77 — Four-day load-profile parsing fix

- Read numeric `load_w` values from rolling load-profile rows.
- Fall back to the shared planning load for incomplete rows.
- Add a coordinator regression case using the actual `{date, load_w}` row shape.
- No planning policy or control behavior changed.
