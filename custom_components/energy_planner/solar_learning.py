"""AC solar shadow learning. All forecasts are frozen before their target hour.

No dependency on HA, no model controls, no future observations in training features.
"""
from __future__ import annotations

import math
from statistics import median

MODEL_VERSION = 2
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
    return "0-3h" if hours < 3 else "3-12h" if hours < 12 else "12-24h"


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
    # Normalize lead labels on persisted v1 outcomes after changing from
    # target-end lead to target-start lead. Forecast observations stay intact.
    for row in [*memory.get("pending", []), *scored]:
        row["lead"] = lead_bucket((row["start"] - row["issued_at"]) / 3600)
        row["model_version"] = MODEL_VERSION
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


def _recovery_key(row):
    issued = number(row.get("issued_at")) if isinstance(row, dict) else None
    start = number(row.get("start")) if isinstance(row, dict) else None
    end = number(row.get("end")) if isinstance(row, dict) else None
    if None in (issued, start, end):
        return None
    return (round(issued, 6), round(start, 3), round(end, 3))


def _normalize_recovery_row(row, now):
    if not isinstance(row, dict):
        return None, "row_not_object"
    issued = number(row.get("issued_at"))
    start = number(row.get("start"))
    end = number(row.get("end"))
    raw = number(row.get("raw_kwh"))
    live = number(row.get("live_kwh"))
    learned = number(row.get("learned_kwh"))
    actual = number(row.get("actual_kwh"))
    coverage = number(row.get("coverage"))
    if None in (issued, start, end, raw, live, learned, coverage):
        return None, "missing_numeric_field"
    if not issued < start < end or end > now + 1:
        return None, "invalid_time_order"
    if min(raw, live, learned) < 0 or not 0 <= coverage <= 1.01:
        return None, "invalid_energy_or_coverage"
    day = row.get("day")
    hour = row.get("hour")
    sky = row.get("sky_bin")
    if not isinstance(day, str) or not day or not isinstance(hour, int) or not 0 <= hour <= 23:
        return None, "invalid_target_metadata"
    if sky not in ("clear", "mixed", "cloudy", "unknown"):
        return None, "invalid_sky_bin"
    accepted = bool(row.get("accepted"))
    if accepted and (actual is None or actual < 0 or coverage < 0.99):
        return None, "invalid_accepted_outcome"
    if actual is not None and actual < 0:
        return None, "invalid_actual_energy"
    normalized = dict(row)
    normalized.update({
        "issued_at": issued, "start": start, "end": end,
        "raw_kwh": raw, "live_kwh": live, "learned_kwh": learned,
        "actual_kwh": actual, "coverage": coverage,
        "lead": lead_bucket((start - issued) / 3600),
        "accepted": accepted, "model_version": MODEL_VERSION,
        "trained": bool(row.get("trained")),
        "training_samples": max(0, int(number(row.get("training_samples")) or 0)),
    })
    if not accepted and not normalized.get("exclusion"):
        normalized["exclusion"] = "recovered_excluded"
    return normalized, None


def merge_recovery(memory, bundle, now):
    """Merge scored recovery evidence without replacing live learner state."""
    if not isinstance(memory, dict) or not isinstance(bundle, dict):
        raise ValueError("Recovery state and bundle must be JSON objects")
    current_identity = memory.get("identity")
    bundle_identity = bundle.get("identity")
    if not current_identity:
        raise ValueError("Current solar source identity is not ready; retry after solar sources finish loading")
    if not bundle_identity or bundle_identity != current_identity:
        raise ValueError("Recovery bundle solar source identity does not match the current installation")
    rows = bundle.get("scored")
    if not isinstance(rows, list):
        raise ValueError("Recovery bundle is missing its scored forecast rows")

    before = scorecard(memory)
    scored = memory.setdefault("scored", [])
    existing_keys = {key for row in scored if (key := _recovery_key(row)) is not None}
    imported = duplicates = rejected = accepted_imported = excluded_imported = 0
    reasons = {}
    for row in rows:
        normalized, reason = _normalize_recovery_row(row, now)
        if reason:
            rejected += 1
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        key = _recovery_key(normalized)
        if key in existing_keys:
            duplicates += 1
            continue
        scored.append(normalized)
        existing_keys.add(key)
        imported += 1
        if normalized.get("accepted"):
            accepted_imported += 1
        else:
            excluded_imported += 1

    cutoff = now - RETENTION_DAYS * 86400
    scored.sort(key=lambda row: (number(row.get("issued_at")) or 0, number(row.get("start")) or 0))
    memory["scored"] = [row for row in scored if (number(row.get("end")) or 0) >= cutoff][-60000:]
    after = scorecard(memory)
    source = bundle.get("recovery") if isinstance(bundle.get("recovery"), dict) else {}
    return {
        "bundle_rows": len(rows),
        "imported_rows": imported,
        "duplicates_skipped": duplicates,
        "rejected_rows": rejected,
        "rejected_reasons": reasons,
        "accepted_rows_imported": accepted_imported,
        "excluded_rows_imported": excluded_imported,
        "scorecard_samples_before": before["accepted_forecasts"],
        "scorecard_samples_after": after["accepted_forecasts"],
        "usable_days_before": before["usable_days"],
        "usable_days_after": after["usable_days"],
        "issued_rows_before": before["issued_forecast_rows"],
        "issued_rows_after": after["issued_forecast_rows"],
        "bundle_source": source.get("source", "external_recovery_bundle"),
    }


def scorecard(memory):
    rows = [r for r in memory.get("scored", []) if r.get("accepted") and r["raw_kwh"] >= 0.05]
    result = {}
    for lead in ("0-3h", "3-12h", "12-24h"):
        issued = [r for r in rows if r["lead"] == lead]
        # Forecasts are refreshed hourly, so the same target hour may have
        # several valid as-issued predictions. Score its latest prediction
        # once per horizon bucket; never treat refreshes as independent samples.
        latest_by_target = {}
        for row in issued:
            previous = latest_by_target.get(row["start"])
            if previous is None or row["issued_at"] > previous["issued_at"]:
                latest_by_target[row["start"]] = row
        group = list(latest_by_target.values())
        trained = [r for r in group if r["trained"]]
        def scores(items):
            output = {"samples": len(items), "unique_target_hours": len({r["start"] for r in items})}
            for name in ("raw", "live", "learned"):
                errors = [r[name + "_kwh"] - r["actual_kwh"] for r in items]
                output[name] = {"mae_kwh": sum(map(abs, errors)) / len(errors) if errors else None,
                                "bias_kwh": sum(errors) / len(errors) if errors else None}
            return output
        result[lead] = {"all": scores(group), "trained_only": scores(trained),
                        "issued_rows": len(issued)}
    return {"by_horizon": result, "usable_days": len({r["day"] for r in rows}),
            "accepted_forecasts": sum(v["all"]["samples"] for v in result.values()),
            "issued_forecast_rows": len(rows),
            "unique_target_hours": len({(r["start"], r["lead"]) for r in rows}), "excluded_forecasts": sum(not r.get("accepted") for r in memory.get("scored", []))}
