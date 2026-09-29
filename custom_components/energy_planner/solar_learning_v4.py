"""Continuous weather-aware solar residual learner (v4 shadow only).

The v4 learner runs beside the categorical v3 learner.  It never changes planner
decisions.  Predictions are based exclusively on information frozen at issuance
and previously completed target hours.
"""
from __future__ import annotations

from datetime import date
import math

MODEL_VERSION = 4
MODEL_NAME = "continuous_weather_residual_v4"
MIN_SAMPLES = 6
MIN_DAYS = 3
MIN_EFFECTIVE_SAMPLES = 3.0
MAX_NEIGHBORS = 24
MAX_CORRECTION_FRACTION = 0.40
BACKFILL_MAX_SCORED_ROWS = 2500
TRAINING_SCAN_MAX_ROWS = 5000


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _lead_hours(row):
    start = number(row.get("start"))
    issued = number(row.get("issued_at"))
    if start is None or issued is None:
        return None
    return max(0.0, (start - issued) / 3600.0)


def _day_ordinal(row):
    value = row.get("day")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).toordinal()
    except ValueError:
        return None


def _hour_distance(left, right):
    try:
        delta = abs(float(left) - float(right))
    except (TypeError, ValueError):
        return None
    return min(delta, 24.0 - delta)


def _observed(row, key):
    observed = row.get("observed_at_issue")
    if not isinstance(observed, dict):
        return None
    return number(observed.get(key))


def _distance_squared(row, candidate):
    """Continuous feature distance using only as-issued forecast/context fields."""
    row_sky = number(row.get("forecast_sky"))
    candidate_sky = number(candidate.get("forecast_sky"))
    if row_sky is None or candidate_sky is None:
        return None

    terms = []

    def add(left, right, scale, weight=1.0):
        if left is None or right is None:
            return
        terms.append(weight * ((left - right) / scale) ** 2)

    # Cloud fraction is the dominant weather feature, but unlike v3 it remains
    # continuous so nearby mixed/cloudy conditions can inform each other.
    add(row_sky, candidate_sky, 0.30, 1.5)

    hour_delta = _hour_distance(row.get("hour"), candidate.get("hour"))
    if hour_delta is not None:
        terms.append((hour_delta / 3.0) ** 2)

    add(_lead_hours(row), _lead_hours(candidate), 8.0, 0.7)

    row_raw = number(row.get("raw_kwh"))
    candidate_raw = number(candidate.get("raw_kwh"))
    if row_raw is not None and candidate_raw is not None:
        add(math.log1p(row_raw), math.log1p(candidate_raw), 0.75, 0.7)

    add(
        number(row.get("forecast_temperature_c")),
        number(candidate.get("forecast_temperature_c")),
        10.0,
        0.25,
    )

    row_day = _day_ordinal(row)
    candidate_day = _day_ordinal(candidate)
    add(row_day, candidate_day, 45.0, 0.20)

    # Local radiation/current PV are useful cloud-persistence signals only for
    # immediate forecasts.  They are deliberately ignored at longer horizons.
    row_lead = _lead_hours(row)
    candidate_lead = _lead_hours(candidate)
    if (
        row_lead is not None
        and candidate_lead is not None
        and row_lead < 3.0
        and candidate_lead < 3.0
    ):
        add(_observed(row, "radiation_wm2"), _observed(candidate, "radiation_wm2"), 250.0, 0.50)
        add(_observed(row, "power_w"), _observed(candidate, "power_w"), 2500.0, 0.30)

    return sum(terms) if terms else None


def _weighted_median(values):
    """Return a robust weighted median for (value, weight) pairs."""
    ordered = sorted((value, weight) for value, weight in values if weight > 0)
    total = sum(weight for _, weight in ordered)
    if not ordered or total <= 0:
        return 0.0
    midpoint = total / 2.0
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= midpoint:
            return value
    return ordered[-1][0]


def prediction(memory, candidate, training_rows=None):
    """Predict with a continuous, distance-weighted residual neighborhood.

    Each historical target hour contributes at most one observation: the issued
    forecast whose frozen features most closely resemble the candidate.  Only
    target hours that had fully completed before candidate issuance are eligible.
    """
    baseline = number(candidate.get("raw_kwh"))
    if baseline is None or baseline < 0.10 or number(candidate.get("forecast_sky")) is None:
        return baseline if baseline is not None else 0.0, False, {
            "training_samples": 0,
            "effective_samples": 0.0,
            "training_days": 0,
            "reason": "missing_baseline_or_cloud",
        }

    best_by_target = {}
    source_rows = (
        list(training_rows)
        if training_rows is not None
        else memory.get("scored", [])[-TRAINING_SCAN_MAX_ROWS:]
    )
    for row in source_rows:
        if not isinstance(row, dict) or not row.get("accepted"):
            continue
        end = number(row.get("end"))
        actual = number(row.get("actual_kwh"))
        raw = number(row.get("raw_kwh"))
        if (
            end is None
            or end > candidate["issued_at"]
            or actual is None
            or raw is None
            or raw < 0.10
        ):
            continue
        distance_sq = _distance_squared(row, candidate)
        if distance_sq is None:
            continue
        key = row.get("start")
        previous = best_by_target.get(key)
        if previous is None or distance_sq < previous[0]:
            best_by_target[key] = (distance_sq, row)

    nearest = sorted(best_by_target.values(), key=lambda item: item[0])[:MAX_NEIGHBORS]
    weighted_rows = []
    for distance_sq, row in nearest:
        weight = math.exp(-0.5 * distance_sq)
        if weight < 0.01:
            continue
        weighted_rows.append((weight, row))

    days = {row.get("day") for _, row in weighted_rows if row.get("day")}
    weight_sum = sum(weight for weight, _ in weighted_rows)
    weight_sq_sum = sum(weight * weight for weight, _ in weighted_rows)
    effective = (
        weight_sum * weight_sum / weight_sq_sum
        if weight_sq_sum > 0
        else 0.0
    )
    metadata = {
        "training_samples": len(weighted_rows),
        "effective_samples": round(effective, 3),
        "training_days": len(days),
        "reason": "trained",
    }

    if (
        len(weighted_rows) < MIN_SAMPLES
        or len(days) < MIN_DAYS
        or effective < MIN_EFFECTIVE_SAMPLES
    ):
        metadata["reason"] = "insufficient_neighbors"
        return baseline, False, metadata

    residual_fractions = []
    for weight, row in weighted_rows:
        raw = number(row.get("raw_kwh"))
        actual = number(row.get("actual_kwh"))
        if raw is None or actual is None:
            continue
        residual_fractions.append(((actual - raw) / max(raw, 0.25), weight))

    residual_fraction = _weighted_median(residual_fractions)
    # Early neighborhoods are intentionally regularized toward Forecast.Solar.
    regularizer = effective / (effective + 8.0)
    correction = residual_fraction * regularizer
    correction = max(-MAX_CORRECTION_FRACTION, min(MAX_CORRECTION_FRACTION, correction))
    metadata["residual_fraction"] = round(residual_fraction, 4)
    metadata["regularizer"] = round(regularizer, 4)
    metadata["correction_fraction"] = round(correction, 4)
    return max(0.0, baseline * (1.0 + correction)), True, metadata


def annotate_row(memory, row, training_rows=None):
    """Freeze a v4 prediction onto one issued row if it has not been frozen."""
    if (
        row.get("v4_model_version") == MODEL_VERSION
        and number(row.get("v4_learned_kwh")) is not None
        and "v4_trained" in row
    ):
        return False
    value, trained, metadata = prediction(memory, row, training_rows=training_rows)
    row.update(
        {
            "v4_learned_kwh": value,
            "v4_trained": trained,
            "v4_training_samples": metadata["training_samples"],
            "v4_effective_samples": metadata["effective_samples"],
            "v4_training_days": metadata["training_days"],
            "v4_training_reason": metadata["reason"],
            "v4_correction_fraction": metadata.get("correction_fraction"),
            "v4_model_version": MODEL_VERSION,
        }
    )
    return True


def ensure_shadow(memory):
    """Backfill/freeze v4 predictions without using future outcomes.

    Backfill is capped because new releases may encounter very large 90-day v3
    datasets.  The most recent rows provide ample honest shadow evidence, and all
    newly issued rows are annotated immediately thereafter.
    """
    scored = memory.get("scored", [])
    candidates = [
        row
        for row in scored[-BACKFILL_MAX_SCORED_ROWS:]
        if isinstance(row, dict) and row.get("v4_model_version") != MODEL_VERSION
    ]
    candidates.extend(
        row
        for row in memory.get("pending", [])
        if isinstance(row, dict) and row.get("v4_model_version") != MODEL_VERSION
    )
    candidates.sort(key=lambda row: (number(row.get("issued_at")) or 0, number(row.get("start")) or 0))
    history_by_end = sorted(
        (row for row in scored if isinstance(row, dict)),
        key=lambda row: number(row.get("end")) or 0,
    )
    eligible = []
    history_index = 0
    changed = 0
    for row in candidates:
        issued_at = number(row.get("issued_at")) or 0
        while (
            history_index < len(history_by_end)
            and (number(history_by_end[history_index].get("end")) or float("inf"))
            <= issued_at
        ):
            eligible.append(history_by_end[history_index])
            history_index += 1
        changed += int(
            annotate_row(
                memory,
                row,
                training_rows=eligible[-TRAINING_SCAN_MAX_ROWS:],
            )
        )
    return {
        "rows_backfilled": changed,
        "backfill_limit": BACKFILL_MAX_SCORED_ROWS,
        "scored_rows_outside_backfill_window": max(0, len(scored) - BACKFILL_MAX_SCORED_ROWS),
    }


def annotate_latest(memory, issued_at):
    """Freeze v4 predictions for rows issued by the current refresh."""
    changed = 0
    for row in memory.get("pending", []):
        if row.get("issued_at") == issued_at:
            changed += int(annotate_row(memory, row))
    return changed


def _scores(items):
    output = {"samples": len(items), "unique_target_hours": len({r["start"] for r in items})}
    for label, key in (
        ("raw", "raw_kwh"),
        ("live", "live_kwh"),
        ("v3", "learned_kwh"),
        ("v4", "v4_learned_kwh"),
    ):
        usable = [
            row for row in items
            if number(row.get(key)) is not None and number(row.get("actual_kwh")) is not None
        ]
        errors = [number(row[key]) - number(row["actual_kwh"]) for row in usable]
        output[label] = {
            "samples": len(errors),
            "mae_kwh": sum(map(abs, errors)) / len(errors) if errors else None,
            "bias_kwh": sum(errors) / len(errors) if errors else None,
        }
    return output


def scorecard(memory):
    """Out-of-sample v4 metrics on the latest issued row per target/horizon."""
    rows = [
        row for row in memory.get("scored", [])
        if row.get("accepted") and (number(row.get("raw_kwh")) or 0) >= 0.05
    ]
    result = {}
    for lead in ("0-3h", "3-12h", "12-24h"):
        issued = [row for row in rows if row.get("lead") == lead]
        latest_by_target = {}
        for row in issued:
            previous = latest_by_target.get(row["start"])
            if previous is None or row["issued_at"] > previous["issued_at"]:
                latest_by_target[row["start"]] = row
        group = list(latest_by_target.values())
        trained = [row for row in group if row.get("v4_trained")]
        result[lead] = {
            "all": _scores(group),
            "trained_only": _scores(trained),
            "issued_rows": len(issued),
        }

    trained_total = sum(group["trained_only"]["samples"] for group in result.values())
    return {
        "model": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "by_horizon": result,
        "trained_forecasts": trained_total,
        "usable_days": len({row.get("day") for row in rows if row.get("day")}),
    }
