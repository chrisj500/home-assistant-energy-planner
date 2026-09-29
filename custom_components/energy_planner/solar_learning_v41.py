"""Tuned continuous solar residual learner (v4.1 shadow only).

v4.1 runs beside the frozen v3 and v4 shadow models. It adds tighter lead-time
locality, target-hour solar geometry, and evidence/performance-scaled correction
limits. It never changes planner decisions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

MODEL_VERSION = "4.1"
MODEL_NAME = "continuous_weather_residual_v4_1"
MIN_SAMPLES = 6
MIN_DAYS = 3
MIN_EFFECTIVE_SAMPLES = 3.0
MAX_NEIGHBORS = 24
MAX_CORRECTION_FRACTION = 0.30
BACKFILL_MAX_SCORED_ROWS = 2500
TRAINING_SCAN_MAX_ROWS = 5000
PERFORMANCE_WINDOW = 24


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


def _hour_distance(left, right):
    try:
        delta = abs(float(left) - float(right))
    except (TypeError, ValueError):
        return None
    return min(delta, 24.0 - delta)


def _azimuth_distance(left, right):
    left = number(left)
    right = number(right)
    if left is None or right is None:
        return None
    delta = abs(left - right) % 360.0
    return min(delta, 360.0 - delta)


def _observed(row, key):
    observed = row.get("observed_at_issue")
    if not isinstance(observed, dict):
        return None
    return number(observed.get(key))


def _lead_window_hours(lead):
    """Hard locality window before continuous lead weighting is applied."""
    if lead is None:
        return None
    if lead < 3.0:
        return 1.5
    if lead < 12.0:
        return 3.0
    return 4.0


def solar_position(timestamp, latitude, longitude):
    """Approximate target solar elevation/azimuth from UTC timestamp and site.

    NOAA-style equations are sufficiently accurate for neighborhood matching and
    avoid any additional runtime dependency or API call. Azimuth is degrees
    clockwise from north.
    """
    ts = number(timestamp)
    lat = number(latitude)
    lon = number(longitude)
    if ts is None or lat is None or lon is None:
        return None, None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None, None

    dt = datetime.fromtimestamp(ts, timezone.utc)
    day = dt.timetuple().tm_yday
    utc_hour = (
        dt.hour
        + dt.minute / 60.0
        + dt.second / 3600.0
        + dt.microsecond / 3_600_000_000.0
    )
    gamma = 2.0 * math.pi / 365.0 * (day - 1 + (utc_hour - 12.0) / 24.0)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2.0 * gamma)
        + 0.000907 * math.sin(2.0 * gamma)
        - 0.002697 * math.cos(3.0 * gamma)
        + 0.00148 * math.sin(3.0 * gamma)
    )

    true_solar_minutes = (utc_hour * 60.0 + eqtime + 4.0 * lon) % 1440.0
    hour_angle_deg = true_solar_minutes / 4.0 - 180.0
    hour_angle = math.radians(hour_angle_deg)
    lat_rad = math.radians(lat)

    cos_zenith = (
        math.sin(lat_rad) * math.sin(decl)
        + math.cos(lat_rad) * math.cos(decl) * math.cos(hour_angle)
    )
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    elevation = 90.0 - math.degrees(math.acos(cos_zenith))

    azimuth = (
        math.degrees(
            math.atan2(
                math.sin(hour_angle),
                math.cos(hour_angle) * math.sin(lat_rad)
                - math.tan(decl) * math.cos(lat_rad),
            )
        )
        + 180.0
    ) % 360.0
    return elevation, azimuth


def ensure_target_geometry(rows, latitude, longitude):
    """Freeze target-midpoint solar geometry onto forecast rows."""
    changed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            number(row.get("target_sun_elevation_deg")) is not None
            and number(row.get("target_sun_azimuth_deg")) is not None
        ):
            continue
        start = number(row.get("start"))
        end = number(row.get("end"))
        if start is None:
            continue
        midpoint = start + ((end - start) / 2.0 if end is not None else 1800.0)
        elevation, azimuth = solar_position(midpoint, latitude, longitude)
        if elevation is None or azimuth is None:
            continue
        row["target_sun_elevation_deg"] = round(elevation, 4)
        row["target_sun_azimuth_deg"] = round(azimuth, 4)
        changed += 1
    return changed


def _distance_squared(row, candidate):
    """Feature distance with strict lead locality and target solar geometry."""
    row_sky = number(row.get("forecast_sky"))
    candidate_sky = number(candidate.get("forecast_sky"))
    if row_sky is None or candidate_sky is None:
        return None

    row_lead = _lead_hours(row)
    candidate_lead = _lead_hours(candidate)
    window = _lead_window_hours(candidate_lead)
    if row_lead is None or candidate_lead is None or window is None:
        return None
    if abs(row_lead - candidate_lead) > window:
        return None

    row_elevation = number(row.get("target_sun_elevation_deg"))
    candidate_elevation = number(candidate.get("target_sun_elevation_deg"))
    row_azimuth = number(row.get("target_sun_azimuth_deg"))
    candidate_azimuth = number(candidate.get("target_sun_azimuth_deg"))
    if (
        row_elevation is None
        or candidate_elevation is None
        or row_azimuth is None
        or candidate_azimuth is None
    ):
        return None

    terms = []

    def add(left, right, scale, weight=1.0):
        if left is None or right is None:
            return
        terms.append(weight * ((left - right) / scale) ** 2)

    # Continuous cloud remains the dominant weather feature.
    add(row_sky, candidate_sky, 0.25, 1.6)

    # Physical target geometry is more useful than clock hour when seasons move.
    add(row_elevation, candidate_elevation, 10.0, 1.8)
    azimuth_delta = _azimuth_distance(row_azimuth, candidate_azimuth)
    if azimuth_delta is not None:
        terms.append(0.65 * (azimuth_delta / 35.0) ** 2)

    # Lead time now has a hard locality window and stronger soft weighting.
    add(row_lead, candidate_lead, max(window / 2.0, 0.75), 1.4)

    hour_delta = _hour_distance(row.get("hour"), candidate.get("hour"))
    if hour_delta is not None:
        terms.append(0.20 * (hour_delta / 4.0) ** 2)

    row_raw = number(row.get("raw_kwh"))
    candidate_raw = number(candidate.get("raw_kwh"))
    if row_raw is not None and candidate_raw is not None:
        add(math.log1p(row_raw), math.log1p(candidate_raw), 0.60, 0.9)

    add(
        number(row.get("forecast_temperature_c")),
        number(candidate.get("forecast_temperature_c")),
        8.0,
        0.20,
    )

    # Local conditions are persistence signals only at truly short horizons.
    if row_lead < 3.0 and candidate_lead < 3.0:
        add(
            _observed(row, "radiation_wm2"),
            _observed(candidate, "radiation_wm2"),
            225.0,
            0.60,
        )
        add(
            _observed(row, "power_w"),
            _observed(candidate, "power_w"),
            2250.0,
            0.35,
        )

    return sum(terms) if terms else None


def _weighted_median(values):
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


def _latest_scored_by_target(rows, lead):
    latest = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not row.get("accepted")
            or row.get("lead") != lead
            or not row.get("v41_trained")
        ):
            continue
        actual = number(row.get("actual_kwh"))
        raw = number(row.get("raw_kwh"))
        learned = number(row.get("v41_learned_kwh"))
        if actual is None or raw is None or learned is None:
            continue
        key = row.get("start")
        previous = latest.get(key)
        if previous is None or number(row.get("issued_at")) > number(previous.get("issued_at")):
            latest[key] = row
    return sorted(
        latest.values(),
        key=lambda row: number(row.get("end")) or 0,
    )[-PERFORMANCE_WINDOW:]


def _performance_scale(training_rows, candidate):
    """Scale trust using only prior frozen v4.1 out-of-sample performance."""
    lead = candidate.get("lead")
    rows = _latest_scored_by_target(training_rows, lead)
    samples = len(rows)
    if samples < 4:
        return 0.35, samples, None, None
    if samples < 8:
        base = 0.50
    else:
        base = 1.0

    raw_errors = [
        abs(number(row["raw_kwh"]) - number(row["actual_kwh"]))
        for row in rows
    ]
    learned_errors = [
        abs(number(row["v41_learned_kwh"]) - number(row["actual_kwh"]))
        for row in rows
    ]
    raw_mae = sum(raw_errors) / samples
    learned_mae = sum(learned_errors) / samples
    ratio = raw_mae / max(learned_mae, 0.05)
    performance = min(1.0, max(0.20, ratio * ratio))
    return min(base, performance), samples, raw_mae, learned_mae


def _evidence_cap(effective_samples, training_days, performance_scale):
    """Maximum correction grows with evidence and demonstrated performance."""
    evidence_cap = min(
        MAX_CORRECTION_FRACTION,
        0.05 + 0.01 * max(0.0, effective_samples),
    )
    day_factor = min(1.0, 0.75 + 0.05 * max(0, training_days - MIN_DAYS))
    return evidence_cap * day_factor * performance_scale


def prediction(memory, candidate, training_rows=None):
    baseline = number(candidate.get("raw_kwh"))
    if (
        baseline is None
        or baseline < 0.10
        or number(candidate.get("forecast_sky")) is None
        or number(candidate.get("target_sun_elevation_deg")) is None
        or number(candidate.get("target_sun_azimuth_deg")) is None
    ):
        return baseline if baseline is not None else 0.0, False, {
            "training_samples": 0,
            "effective_samples": 0.0,
            "training_days": 0,
            "reason": "missing_baseline_cloud_or_geometry",
        }

    source_rows = (
        list(training_rows)
        if training_rows is not None
        else memory.get("scored", [])[-TRAINING_SCAN_MAX_ROWS:]
    )
    best_by_target = {}
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
        if weight >= 0.01:
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
        "lead_window_hours": _lead_window_hours(_lead_hours(candidate)),
        "reason": "trained",
    }
    if (
        len(weighted_rows) < MIN_SAMPLES
        or len(days) < MIN_DAYS
        or effective < MIN_EFFECTIVE_SAMPLES
    ):
        metadata["reason"] = "insufficient_local_neighbors"
        return baseline, False, metadata

    residual_fractions = []
    for weight, row in weighted_rows:
        raw = number(row.get("raw_kwh"))
        actual = number(row.get("actual_kwh"))
        if raw is None or actual is None:
            continue
        residual_fractions.append(((actual - raw) / max(raw, 0.25), weight))

    residual_fraction = _weighted_median(residual_fractions)
    regularizer = effective / (effective + 12.0)
    performance_scale, performance_samples, raw_mae, learned_mae = _performance_scale(
        source_rows,
        candidate,
    )
    cap = _evidence_cap(effective, len(days), performance_scale)
    correction = residual_fraction * regularizer
    correction = max(-cap, min(cap, correction))

    metadata.update(
        {
            "residual_fraction": round(residual_fraction, 4),
            "regularizer": round(regularizer, 4),
            "performance_scale": round(performance_scale, 4),
            "performance_samples": performance_samples,
            "performance_raw_mae_kwh": round(raw_mae, 4) if raw_mae is not None else None,
            "performance_v41_mae_kwh": round(learned_mae, 4) if learned_mae is not None else None,
            "correction_cap_fraction": round(cap, 4),
            "correction_fraction": round(correction, 4),
        }
    )
    return max(0.0, baseline * (1.0 + correction)), True, metadata


def annotate_row(memory, row, training_rows=None):
    if (
        row.get("v41_model_version") == MODEL_VERSION
        and number(row.get("v41_learned_kwh")) is not None
        and "v41_trained" in row
    ):
        return False
    value, trained, metadata = prediction(memory, row, training_rows=training_rows)
    row.update(
        {
            "v41_learned_kwh": value,
            "v41_trained": trained,
            "v41_training_samples": metadata["training_samples"],
            "v41_effective_samples": metadata["effective_samples"],
            "v41_training_days": metadata["training_days"],
            "v41_training_reason": metadata["reason"],
            "v41_lead_window_hours": metadata.get("lead_window_hours"),
            "v41_performance_scale": metadata.get("performance_scale"),
            "v41_performance_samples": metadata.get("performance_samples"),
            "v41_performance_raw_mae_kwh": metadata.get("performance_raw_mae_kwh"),
            "v41_performance_mae_kwh": metadata.get("performance_v41_mae_kwh"),
            "v41_correction_cap_fraction": metadata.get("correction_cap_fraction"),
            "v41_correction_fraction": metadata.get("correction_fraction"),
            "v41_model_version": MODEL_VERSION,
        }
    )
    return True


def ensure_shadow(memory, latitude, longitude):
    """Backfill v4.1 chronologically without future-outcome leakage."""
    scored = memory.get("scored", [])
    pending = memory.get("pending", [])
    geometry_rows = ensure_target_geometry(
        [row for row in (*scored, *pending) if isinstance(row, dict)],
        latitude,
        longitude,
    )
    candidates = [
        row
        for row in scored[-BACKFILL_MAX_SCORED_ROWS:]
        if isinstance(row, dict) and row.get("v41_model_version") != MODEL_VERSION
    ]
    candidates.extend(
        row
        for row in pending
        if isinstance(row, dict) and row.get("v41_model_version") != MODEL_VERSION
    )
    candidates.sort(
        key=lambda row: (
            number(row.get("issued_at")) or 0,
            number(row.get("start")) or 0,
        )
    )
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
        "geometry_rows_annotated": geometry_rows,
        "backfill_limit": BACKFILL_MAX_SCORED_ROWS,
        "scored_rows_outside_backfill_window": max(
            0,
            len(scored) - BACKFILL_MAX_SCORED_ROWS,
        ),
    }


def annotate_latest(memory, issued_at, latitude, longitude):
    rows = [
        row
        for row in memory.get("pending", [])
        if row.get("issued_at") == issued_at
    ]
    ensure_target_geometry(rows, latitude, longitude)
    changed = 0
    for row in rows:
        changed += int(annotate_row(memory, row))
    return changed


def _scores(items):
    output = {
        "samples": len(items),
        "unique_target_hours": len({row["start"] for row in items}),
    }
    for label, key in (
        ("raw", "raw_kwh"),
        ("live", "live_kwh"),
        ("v3", "learned_kwh"),
        ("v4", "v4_learned_kwh"),
        ("v4_1", "v41_learned_kwh"),
    ):
        usable = [
            row
            for row in items
            if number(row.get(key)) is not None
            and number(row.get("actual_kwh")) is not None
        ]
        errors = [
            number(row[key]) - number(row["actual_kwh"])
            for row in usable
        ]
        output[label] = {
            "samples": len(errors),
            "mae_kwh": sum(map(abs, errors)) / len(errors) if errors else None,
            "bias_kwh": sum(errors) / len(errors) if errors else None,
        }
    return output


def scorecard(memory):
    rows = [
        row
        for row in memory.get("scored", [])
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
        trained = [row for row in group if row.get("v41_trained")]
        result[lead] = {
            "all": _scores(group),
            "trained_only": _scores(trained),
            "issued_rows": len(issued),
        }

    trained_total = sum(
        group["trained_only"]["samples"]
        for group in result.values()
    )
    return {
        "model": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "by_horizon": result,
        "trained_forecasts": trained_total,
        "usable_days": len({row.get("day") for row in rows if row.get("day")}),
    }
