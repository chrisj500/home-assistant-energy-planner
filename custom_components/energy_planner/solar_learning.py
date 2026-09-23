"""AC solar shadow learning. All forecasts are frozen before their target hour.

No dependency on HA, no model controls, no future observations in training features.
"""
from __future__ import annotations

import math
from statistics import median

MODEL_VERSION = 1
RETENTION_DAYS = 90
MIN_DAYS = 7
MIN_SAMPLES = 8
MAX_GAP_SECONDS = 180


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def lead_bucket(hours):
    return "1-3h" if hours <= 3 else "3-12h" if hours <= 12 else "12-25h"


def sky_bucket(value):
    value = number(value)
    return "unknown" if value is None else "clear" if value < 0.3 else "mixed" if value < 0.8 else "cloudy"


def observe(memory, sample):
    """Integrate valid AC power with complete coverage; never bridge outages."""
    previous = memory.get("previous")
    memory["previous"] = sample
    hours = memory.setdefault("actual_hours", {})
    if not previous:
        return
    start, end = previous["at"], sample["at"]
    span = end - start
    if not 0 < span <= MAX_GAP_SECONDS:
        return
    valid = previous.get("valid") and sample.get("valid")
    # A reset in the optional lifetime meter invalidates this segment.
    old_energy, new_energy = previous.get("energy_kwh"), sample.get("energy_kwh")
    if old_energy is not None and new_energy is not None and new_energy < old_energy:
        valid = False
    cursor = start
    while cursor < end:
        hour = int(cursor // 3600) * 3600
        stop = min(end, hour + 3600)
        row = hours.setdefault(str(hour), {"kwh": 0.0, "coverage_seconds": 0.0, "invalid_seconds": 0.0})
        if valid:
            # Linear interpolation splits cross-hour segments without duplication.
            p0 = previous["power_w"] + (sample["power_w"] - previous["power_w"]) * (cursor - start) / span
            p1 = previous["power_w"] + (sample["power_w"] - previous["power_w"]) * (stop - start) / span
            row["kwh"] += (p0 + p1) / 2 * (stop - cursor) / 3_600_000
            row["coverage_seconds"] += stop - cursor
        else:
            row["invalid_seconds"] += stop - cursor
        cursor = stop


def prediction(memory, candidate):
    """Regularized, bounded residual learner grouped by local hour/lead/clouds."""
    matches = {}
    for row in memory.get("scored", []):
        if row["end"] > candidate["issued_at"] or not row.get("accepted"):
            continue
        if (row["hour"], row["lead"], row["sky_bin"]) != (candidate["hour"], candidate["lead"], candidate["sky_bin"]):
            continue
        # Multiple forecasts of one target hour are not independent observations.
        matches[row["start"]] = row
    rows = list(matches.values())
    days = {row["day"] for row in rows}
    baseline = candidate["raw_kwh"]
    if len(rows) < MIN_SAMPLES or len(days) < MIN_DAYS or baseline < 0.05:
        return baseline, False, len(rows)
    residual = median(row["actual_kwh"] - row["raw_kwh"] for row in rows)
    residual *= len(rows) / (len(rows) + 20)
    limit = baseline * 0.25
    return max(0.0, baseline + max(-limit, min(limit, residual))), True, len(rows)


def issue(memory, candidates):
    pending = memory.setdefault("pending", [])
    for row in candidates:
        value, trained, count = prediction(memory, row)
        row = dict(row, learned_kwh=value, trained=trained, training_samples=count, model_version=MODEL_VERSION)
        pending.append(row)
    memory["last_issue"] = candidates[0]["issued_at"] if candidates else memory.get("last_issue")


def finalize(memory, now):
    pending = []
    scored = memory.setdefault("scored", [])
    actual = memory.setdefault("actual_hours", {})
    for row in memory.get("pending", []):
        if row["end"] > now:
            pending.append(row)
            continue
        observation = actual.get(str(int(row["start"])), {})
        coverage = observation.get("coverage_seconds", 0.0)
        accepted = coverage >= 3564 and observation.get("invalid_seconds", 0) == 0
        scored.append(dict(row, actual_kwh=observation.get("kwh"), coverage=coverage / 3600,
                           accepted=accepted, exclusion=None if accepted else "incomplete_or_invalid_production"))
    memory["pending"] = pending
    cutoff = now - RETENTION_DAYS * 86400
    memory["scored"] = [r for r in scored if r["end"] >= cutoff][-60000:]
    memory["actual_hours"] = {k: v for k, v in actual.items() if int(k) >= cutoff}


def scorecard(memory):
    rows = [r for r in memory.get("scored", []) if r.get("accepted") and r["raw_kwh"] >= 0.05]
    result = {}
    for lead in ("1-3h", "3-12h", "12-25h"):
        group = [r for r in rows if r["lead"] == lead]
        trained = [r for r in group if r["trained"]]
        def scores(items):
            output = {"samples": len(items), "unique_target_hours": len({r["start"] for r in items})}
            for name in ("raw", "live", "learned"):
                errors = [r[name + "_kwh"] - r["actual_kwh"] for r in items]
                output[name] = {"mae_kwh": sum(map(abs, errors)) / len(errors) if errors else None,
                                "bias_kwh": sum(errors) / len(errors) if errors else None}
            return output
        result[lead] = {"all": scores(group), "trained_only": scores(trained)}
    return {"by_horizon": result, "usable_days": len({r["day"] for r in rows}),
            "accepted_forecasts": len(rows), "excluded_forecasts": sum(not r.get("accepted") for r in memory.get("scored", []))}
