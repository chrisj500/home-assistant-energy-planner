"""Direct, provider-energy-independent solar production model for 24-96h."""
from __future__ import annotations

import math
from datetime import datetime, timezone

try:
    from .solar_learning import lead_bucket, number
    from .solar_learning_v41 import solar_position
except ImportError:
    from solar_learning import lead_bucket, number
    from solar_learning_v41 import solar_position

MODEL_VERSION = 1
HORIZONS = ("24-48h", "48-72h", "72-96h")
MIN_NEIGHBORS = 12
MIN_DAYS = 5
MIN_EFFECTIVE = 5.0
MAX_NEIGHBORS = 36


def target_geometry(row, latitude, longitude):
    start, end = number(row.get("start")), number(row.get("end"))
    if start is None or end is None or end <= start:
        return None, None
    return solar_position((start + end) / 2, latitude, longitude)


def _feature_distance(row, candidate):
    terms = []

    def add(a, b, scale, weight=1.0):
        a, b = number(a), number(b)
        if a is not None and b is not None:
            terms.append(weight * ((a - b) / scale) ** 2)

    add(row.get("forecast_sky"), candidate.get("forecast_sky"), 0.25, 1.5)
    add(row.get("target_sun_elevation_deg"), candidate.get("target_sun_elevation_deg"), 10.0, 1.0)
    add(row.get("forecast_temperature_c"), candidate.get("forecast_temperature_c"), 8.0, 0.2)
    add(row.get("hour"), candidate.get("hour"), 2.5, 1.0)
    add(row.get("lead_hours"), candidate.get("lead_hours"), 12.0, 0.6)
    add(row.get("day_of_year"), candidate.get("day_of_year"), 45.0, 0.25)
    left, right = number(row.get("target_sun_azimuth_deg")), number(candidate.get("target_sun_azimuth_deg"))
    if left is not None and right is not None:
        delta = abs((left - right + 180.0) % 360.0 - 180.0)
        terms.append((delta / 35.0) ** 2)
    return sum(terms) if terms else None


def prediction(memory, candidate):
    """Weighted-neighbor AC energy forecast, without raw solar-energy input."""
    baseline = 0.0
    sky = number(candidate.get("forecast_sky"))
    elevation = number(candidate.get("target_sun_elevation_deg"))
    lead = number(candidate.get("lead_hours"))
    if sky is None or not 0 <= sky <= 1 or elevation is None or elevation <= 5 or lead is None:
        return baseline, False, {"reason": "missing_cloud_or_solar_geometry", "samples": 0}
    horizon = lead_bucket(lead)
    if horizon not in HORIZONS:
        return baseline, False, {"reason": "outside_24_to_96h_horizon", "samples": 0}

    issued = number(candidate.get("issued_at"))
    latest_by_target = {}
    for row in memory.get("scored", []):
        if not isinstance(row, dict) or not row.get("accepted") or row.get("lead") != horizon:
            continue
        end = number(row.get("end"))
        actual = number(row.get("actual_kwh"))
        start = number(row.get("start"))
        if end is None or actual is None or start is None or issued is None or end > issued:
            continue
        if actual < 0 or not all(number(row.get(key)) is not None for key in (
            "forecast_sky", "target_sun_elevation_deg", "target_sun_azimuth_deg", "day_of_year"
        )):
            continue
        distance = _feature_distance(row, candidate)
        if distance is None:
            continue
        previous = latest_by_target.get(start)
        if previous is None or distance < previous[0]:
            latest_by_target[start] = (distance, row)

    nearest = sorted(latest_by_target.values(), key=lambda item: item[0])[:MAX_NEIGHBORS]
    weighted = [(math.exp(-0.5 * distance), row) for distance, row in nearest
                if math.exp(-0.5 * distance) >= 0.01]
    days = {row.get("day") for _, row in weighted if row.get("day")}
    weight_sum = sum(weight for weight, _ in weighted)
    weight_sq_sum = sum(weight * weight for weight, _ in weighted)
    effective = weight_sum * weight_sum / weight_sq_sum if weight_sq_sum else 0.0
    metadata = {"reason": "insufficient_neighbors", "samples": len(weighted),
                "days": len(days), "effective_samples": round(effective, 3),
                "horizon": horizon}
    if len(weighted) < MIN_NEIGHBORS or len(days) < MIN_DAYS or effective < MIN_EFFECTIVE:
        return baseline, False, metadata

    ordered = sorted((number(row.get("actual_kwh")), weight) for weight, row in weighted)
    midpoint, running, total = sum(weight for _, weight in ordered) / 2.0, 0.0, sum(weight for _, weight in ordered)
    estimate = ordered[-1][0]
    for value, weight in ordered:
        running += weight
        if running >= midpoint:
            estimate = value
            break
    metadata.update(reason="trained", prediction_kwh=max(estimate, 0.0))
    return max(estimate, 0.0), True, metadata


def annotate_latest(memory, issued_at, latitude, longitude):
    """Freeze independent profile predictions on current 24-96h rows."""
    for row in memory.get("pending", []):
        if row.get("issued_at") != issued_at or "profile_model_version" in row:
            continue
        lead = number(row.get("lead_hours"))
        if lead is None or not 24 <= lead < 96:
            continue
        elevation, azimuth = target_geometry(row, latitude, longitude)
        row["target_sun_elevation_deg"] = elevation
        row["target_sun_azimuth_deg"] = azimuth
        start = number(row.get("start"))
        row["day_of_year"] = datetime.fromtimestamp(start, timezone.utc).timetuple().tm_yday if start else None
        value, active, metadata = prediction(memory, row)
        row.update(profile_model_version=MODEL_VERSION, profile_kwh=value,
                   profile_trained=active, profile_metadata=metadata)


def scorecard(memory):
    """Prospective direct-model scores on targets with trained predictions."""
    result = {}
    for horizon in HORIZONS:
        by_target = {}
        issued_rows = [row for row in memory.get("scored", [])
                       if row.get("accepted") and row.get("profile_model_version") == MODEL_VERSION
                       and row.get("lead") == horizon]
        for row in issued_rows:
            previous = by_target.get(row.get("start"))
            if previous is None or row.get("issued_at", 0) > previous.get("issued_at", 0):
                by_target[row.get("start")] = row
        rows = [row for row in by_target.values() if row.get("profile_trained")]
        scores = {"samples": len(rows), "unique_target_hours": len(rows),
                  "days": len({row.get("day") for row in rows})}
        actual = [number(row.get("actual_kwh")) for row in rows]
        for name, key in (("profile", "profile_kwh"), ("raw", "raw_kwh"), ("v4", "v4_learned_kwh")):
            pairs = [(number(row.get(key)), number(row.get("actual_kwh"))) for row in rows]
            pairs = [(forecast, observed) for forecast, observed in pairs
                     if forecast is not None and observed is not None]
            errors = [forecast - observed for forecast, observed in pairs]
            scores[name] = {"samples": len(errors),
                            "mae_kwh": sum(map(abs, errors)) / len(errors) if errors else None,
                            "bias_kwh": sum(errors) / len(errors) if errors else None}
        raw_mae, profile_mae = scores["raw"]["mae_kwh"], scores["profile"]["mae_kwh"]
        raw_bias, profile_bias = scores["raw"]["bias_kwh"], scores["profile"]["bias_kwh"]
        scores["beats_raw"] = bool(
            raw_mae is not None and profile_mae is not None and profile_mae < raw_mae
            and raw_bias is not None and profile_bias is not None and profile_bias >= raw_bias
        )
        result[horizon] = scores
    active_rows = [row for row in memory.get("scored", [])
                   if row.get("accepted") and row.get("profile_trained")
                   and row.get("profile_model_version") == MODEL_VERSION]
    return {"model": "direct_weather_conditioned_ac_profile_v1", "evaluation": "prospective_only",
            "forecast_applied": False, "by_horizon": result,
            "trained_forecasts": sum(item["samples"] for item in result.values()),
            "usable_days": len({row.get("day") for row in active_rows})}
