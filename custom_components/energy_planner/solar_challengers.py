"""Prospective-only solar challengers. No planner or equipment control outputs."""
from __future__ import annotations

from datetime import datetime, timezone
import math

try:
    from .forecast_solar_shadow import power_at
    from .solar_learning import number
    from .solar_learning_v41 import solar_position
except ImportError:  # Standalone regression tests.
    from forecast_solar_shadow import power_at
    from solar_learning import number
    from solar_learning_v41 import solar_position

VERSION = 1
HORIZONS = ("0-3h", "3-12h", "12-24h")
WINDOW = 48
MIN_SAMPLES = 12
MIN_DAYS = 3
MODEL_KEYS = {
    "raw": "raw_kwh", "live": "live_kwh", "v3": "learned_kwh",
    "v4": "v4_learned_kwh", "v41": "v41_learned_kwh",
}
TRAINED_KEYS = {"v3": "trained", "v4": "v4_trained", "v41": "v41_trained"}
PERSISTENCE_SECONDS = 7200


def _energy(value):
    value = number(value)
    return value if value is not None and value >= 0 else None


def _latest(rows):
    latest = {}
    for row in rows:
        key = row["start"]
        if key not in latest or row["issued_at"] > latest[key]["issued_at"]:
            latest[key] = row
    return list(latest.values())


def blend_prediction(memory, candidate):
    """Shrink inverse-MAE weights toward the baseline, separately per horizon.

    All member errors use the same latest-per-target rows. Only observations
    completed before issuance are eligible; repeated issuance adds no evidence.
    Current untrained or baseline-identical members add no duplicate weight.
    """
    baseline = "live" if candidate["lead"] == "0-3h" else "raw"
    base = _energy(candidate.get(MODEL_KEYS[baseline]))
    if base is None:
        baseline, base = "raw", _energy(candidate.get("raw_kwh")) or 0.0
    values = {baseline: base}
    for name, key in MODEL_KEYS.items():
        value = _energy(candidate.get(key))
        if name in TRAINED_KEYS and not candidate.get(TRAINED_KEYS[name]):
            continue
        if name == "live" and candidate["lead"] != "0-3h":
            continue
        if value is not None and not any(abs(value - other) < 1e-9 for other in values.values()):
            values[name] = value
    meta = {"weights": {baseline: 1.0}, "samples": 0, "days": 0,
            "reason": "insufficient_evidence", "baseline": baseline, "mae_kwh": {}}
    if len(values) == 1:
        meta["reason"] = "baseline_only"
        return base, False, meta

    issued = candidate["issued_at"]
    rows = [r for r in memory.get("scored", [])
            if r.get("accepted") and r.get("lead") == candidate["lead"]
            and number(r.get("end")) is not None
            and issued - 21 * 86400 <= r["end"] <= issued
            and number(r.get("issued_at")) is not None and r["issued_at"] < r["start"]
            and _energy(r.get("actual_kwh")) is not None
            and (_energy(r.get("raw_kwh")) or 0) >= 0.05]
    # Select latest before filtering the matched cohort: never substitute an
    # older, more favorable prediction when the latest one lacks a member.
    rows = [r for r in _latest(rows)
            if all(_energy(r.get(MODEL_KEYS[name])) is not None for name in values)]
    rows = sorted(rows, key=lambda r: r["end"])[-WINDOW:]
    days = len({r["day"] for r in rows})
    meta.update(samples=len(rows), days=days)
    if len(rows) < MIN_SAMPLES or days < MIN_DAYS:
        return base, False, meta
    errors = {name: sum(abs(r[MODEL_KEYS[name]] - r["actual_kwh"]) for r in rows) / len(rows)
              for name in values}
    inverse = {name: 1.0 / max(error, 0.10) for name, error in errors.items()}
    total = sum(inverse.values())
    authority = min(0.75, len(rows) / (len(rows) + 24) * min(1.0, days / 7))
    weights = {name: authority * value / total for name, value in inverse.items()}
    weights[baseline] += 1.0 - authority
    meta.update(weights=weights, mae_kwh=errors, authority=authority, reason="weighted")
    return sum(values[name] * weight for name, weight in weights.items()), True, meta


def clear_sky_ghi(elevation):
    """Haurwitz GHI shape; approximate geometric elevation, not a roof AC model.

    Formula reference: pvlib.clearsky.haurwitz. Refraction is neglected here;
    issuance below 10 degrees is excluded to avoid low-sun amplification.
    """
    sine = math.sin(math.radians(elevation))
    return 1098.0 * sine * math.exp(-0.059 / sine) if sine > 0 else 0.0


def persistence_prediction(candidate, points, latitude, longitude):
    """Normalize current AC power by clear-sky GHI and fade to raw in two hours.

    This assumes AC production follows horizontal clear-sky shape locally. It
    does not infer roof geometry, future clouds, shading, or inverter limits.
    """
    raw = _energy(candidate.get("raw_kwh")) or 0.0
    meta = {"reason": "outside_two_hour_horizon", "shape": "haurwitz_ghi_proxy",
            "fade_minutes": 120}
    issued, start, end = (number(candidate.get(k)) for k in ("issued_at", "start", "end"))
    if None in (issued, start, end) or not issued <= start < end:
        meta["reason"] = "invalid_target"
        return raw, False, meta
    if start >= issued + PERSISTENCE_SECONDS:
        return raw, False, meta
    observed = candidate.get("observed_at_issue") or {}
    power = _energy(observed.get("power_w"))
    at = number(observed.get("at"))
    if not observed.get("valid") or power is None or at is None or not 0 <= issued - at <= 300:
        meta["reason"] = "production_unavailable"
        return raw, False, meta
    elevation, _ = solar_position(issued, latitude, longitude)
    if elevation is None or elevation < 10:
        meta["reason"] = "low_sun_or_missing_geometry"
        return raw, False, meta
    if (len(points) < 2 or points[0].at.timestamp() > start or points[-1].at.timestamp() < end
            or any(_energy(p.watts) is None for p in points)
            or any(not 0 < (b.at - a.at).total_seconds() <= 7200
                   for a, b in zip(points, points[1:]) if a.at.timestamp() < end and b.at.timestamp() > start)):
        meta["reason"] = "incomplete_provider_curve"
        return raw, False, meta
    ghi_now = clear_sky_ghi(elevation)
    energy = 0.0
    cursor = start
    while cursor < end:
        stop = min(end, cursor + 60)
        mid = (cursor + stop) / 2
        at_mid = datetime.fromtimestamp(mid, timezone.utc)
        elevation_mid, _ = solar_position(mid, latitude, longitude)
        ratio = min(3.0, clear_sky_ghi(elevation_mid) / ghi_now)
        persistent_w = power * ratio
        weight = max(0.0, 1.0 - (mid - issued) / PERSISTENCE_SECONDS)
        energy += (weight * persistent_w + (1 - weight) * power_at(points, at_mid)) * (stop - cursor) / 3_600_000
        cursor = stop
    meta.update(reason="active", clear_sky_ghi_now_wm2=ghi_now, anchor_power_w=power)
    return max(0.0, energy), True, meta


def annotate_latest(memory, issued_at, points, latitude, longitude):
    """Freeze only newly issued forecasts; never backfill historical outcomes."""
    for row in memory.get("pending", []):
        if row.get("issued_at") != issued_at or "challenger_version" in row:
            continue
        blend, ready, blend_meta = blend_prediction(memory, row)
        persistence, active, persistence_meta = persistence_prediction(row, points, latitude, longitude)
        row.update(challenger_version=VERSION, blend_kwh=blend, blend_active=ready,
                   blend_metadata=blend_meta, persistence_kwh=persistence,
                   persistence_active=active, persistence_metadata=persistence_meta)


def _scores(rows):
    scores = {"samples": len(rows), "unique_target_hours": len({r["start"] for r in rows})}
    for name, key in {**MODEL_KEYS, "blend": "blend_kwh", "persistence": "persistence_kwh"}.items():
        errors = [r[key] - r["actual_kwh"] for r in rows]
        scores[name] = {"samples": len(errors),
                        "mae_kwh": sum(map(abs, errors)) / len(errors) if errors else None,
                        "bias_kwh": sum(errors) / len(errors) if errors else None}
    return scores


def scorecard(memory, model):
    """Matched prospective cohorts; active-only separates true use from fallback."""
    if model not in ("blend", "persistence"):
        raise ValueError("Unknown solar challenger")
    keys = [*MODEL_KEYS.values(), "blend_kwh", "persistence_kwh", "actual_kwh"]
    rows = [r for r in memory.get("scored", [])
            if r.get("challenger_version") == VERSION and r.get("accepted")
            and (_energy(r.get("raw_kwh")) or 0) >= 0.05]
    result = {}
    for horizon in HORIZONS:
        issued = [r for r in rows if r.get("lead") == horizon]
        group = [r for r in _latest(issued) if all(_energy(r.get(k)) is not None for k in keys)]
        result[horizon] = {"all": _scores(group),
                           "active_only": _scores([r for r in group if r.get(model + "_active")]),
                           "issued_rows": len(issued)}
    return {"by_horizon": result, "evaluation": "prospective_only",
            "scored_forecasts": sum(v["all"]["samples"] for v in result.values()),
            "active_forecasts": sum(v["active_only"]["samples"] for v in result.values()),
            "usable_days": len({r["day"] for r in rows}),
            "excluded_forecasts": sum(r.get("challenger_version") == VERSION and not r.get("accepted")
                                      for r in memory.get("scored", []))}
