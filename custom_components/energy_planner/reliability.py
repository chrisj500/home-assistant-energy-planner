"""Evidence and fail-closed decision policy; no Home Assistant dependencies."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite


MIN_EVIDENCE_SAMPLES = 3
RELIABILITY_RECORD_SCHEMA_VERSION = 2


def _record_capacity(record, fallback_capacity_kwh=None):
    for key in (
        "capacity_kwh",
        "forecast_capacity_kwh",
        "settled_capacity_kwh",
    ):
        value = number(record.get(key))
        if value is not None and value > 0:
            return value
    fallback = number(fallback_capacity_kwh)
    return fallback if fallback is not None and fallback > 0 else None


def migrate_records(records, *, fallback_capacity_kwh=None):
    """Upgrade historical SOC-error records to topology-aware kWh records.

    Legacy records are preserved. Before automatic topology discovery, the
    configured planner capacity was the capacity used to produce and score those
    forecasts, so it is the best available migration basis for records that
    predate explicit topology metadata.
    """
    migrated = []
    changed = False
    inferred = 0
    for original in records or []:
        row = dict(original)
        capacity = _record_capacity(row, fallback_capacity_kwh)
        error_soc = number(row.get("error_soc"))
        predicted_soc = number(row.get("predicted_soc"))
        actual_soc = number(row.get("actual_soc"))

        if number(row.get("error_kwh")) is None and error_soc is not None and capacity:
            row["error_kwh"] = error_soc / 100.0 * capacity
            changed = True
        if (
            number(row.get("predicted_stored_kwh")) is None
            and predicted_soc is not None
            and capacity
        ):
            row["predicted_stored_kwh"] = predicted_soc / 100.0 * capacity
            changed = True
        if (
            number(row.get("actual_stored_kwh")) is None
            and actual_soc is not None
            and capacity
        ):
            row["actual_stored_kwh"] = actual_soc / 100.0 * capacity
            changed = True
        if capacity is not None and number(row.get("capacity_kwh")) is None:
            row["capacity_kwh"] = capacity
            row["capacity_inferred"] = True
            inferred += 1
            changed = True
        if capacity is not None and row.get("topology_signature") is None:
            row["topology_signature"] = {
                "capacity_kwh": round(capacity, 3),
                "pack_counts": None,
            }
            row["topology_metadata_inferred"] = True
            changed = True
        if row.get("record_schema") != RELIABILITY_RECORD_SCHEMA_VERSION:
            row["record_schema"] = RELIABILITY_RECORD_SCHEMA_VERSION
            changed = True
        migrated.append(row)
    return migrated, {
        "changed": changed,
        "records": len(migrated),
        "capacity_inferred_records": inferred,
    }


def _record_error(record, *, current_capacity_kwh=None):
    """Return topology-normalized SOC error plus physical kWh error."""
    current_capacity = number(current_capacity_kwh)
    error_kwh = number(record.get("error_kwh"))
    historical_capacity = _record_capacity(record)
    error_soc = number(record.get("error_soc"))

    if error_kwh is None and error_soc is not None and historical_capacity:
        error_kwh = error_soc / 100.0 * historical_capacity

    if error_kwh is not None and current_capacity is not None and current_capacity > 0:
        return error_kwh / current_capacity * 100.0, error_kwh

    if error_soc is not None:
        if (
            historical_capacity is not None
            and current_capacity is not None
            and current_capacity > 0
        ):
            normalized_soc = (
                error_soc * historical_capacity / current_capacity
            )
            return normalized_soc, error_soc / 100.0 * historical_capacity
        return error_soc, error_kwh

    return None, error_kwh


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def evidence(records, lead, *, capacity_kwh=None):
    pairs = [
        _record_error(record, current_capacity_kwh=capacity_kwh)
        for record in records
        if record.get("lead") == lead
    ][-30:]
    signed = [soc for soc, _kwh in pairs if soc is not None]
    signed_kwh = [kwh for _soc, kwh in pairs if kwh is not None]
    errors = [abs(v) for v in signed]
    error_kwh = [abs(v) for v in signed_kwh]

    # Engineering floor for the DISPLAY envelope only; this is deliberately
    # two-sided and is not used directly to create export headroom.
    width = max([10.0 + 3.0 * lead, *errors])
    mae = sum(errors) / len(errors) if errors else None
    mae_kwh = sum(error_kwh) / len(error_kwh) if error_kwh else None

    # Physical kWh error is retained across topology changes. For display and
    # SOC-based policy thresholds, that error is re-expressed against the
    # CURRENT battery capacity, so adding capacity does not erase evidence and
    # does not overstate the same historical energy miss in percentage points.
    signed_bias = sum(signed) / len(signed) if signed else None
    signed_bias_kwh = (
        sum(signed_kwh) / len(signed_kwh) if signed_kwh else None
    )
    export_underprediction_bias = (
        max(float(signed_bias), 0.0) if signed_bias is not None else 0.0
    )
    export_underprediction_bias_kwh = (
        max(float(signed_bias_kwh), 0.0)
        if signed_bias_kwh is not None
        else 0.0
    )

    confidence = "learning" if len(errors) < MIN_EVIDENCE_SAMPLES else "low"
    if len(errors) >= MIN_EVIDENCE_SAMPLES and mae <= 10:
        confidence = "medium"
    if len(errors) >= 10 and mae <= 5:
        confidence = "high"
    return {
        "samples": len(errors),
        "mae_soc": mae,
        "mae_kwh": mae_kwh,
        "width_soc": width,
        "signed_bias_soc": signed_bias,
        "signed_bias_kwh": signed_bias_kwh,
        "export_underprediction_bias_soc": export_underprediction_bias,
        "export_underprediction_bias_kwh": export_underprediction_bias_kwh,
        "confidence": confidence,
    }


def sunset_envelope(*, now, sunrise, sunset, target_date, current_soc,
                    point_soc, low_soc, high_soc, historical_width, reserve_floor):
    """Bound remaining uncertainty; daylight simulation never discharges batteries.

    Historical sunset errors cover a full day. Only the unobserved share of
    today's daylight contributes to the current-day historical error allowance.
    Future days retain their full horizon-specific allowance.
    """
    remaining_fraction = 1.0
    daylight = (sunset - sunrise).total_seconds()
    if target_date == now.date() and daylight > 0:
        remaining_fraction = min(max((sunset - now).total_seconds() / daylight, 0.0), 1.0)
    width = historical_width * remaining_fraction
    floor = max(0.0, min(float(reserve_floor), 100.0))
    if target_date == now.date() and sunrise <= now < sunset:
        floor = max(floor, float(current_soc))
    lower = max(floor, min(float(low_soc), float(point_soc) - width))
    upper = max(lower, min(100.0, max(float(high_soc), float(point_soc) + width)))
    return round(lower, 1), round(upper, 1), round(remaining_fraction, 3)


def observe(history, *, now, revision, rows, candidate):
    """Count successful provider refreshes, never minute-by-minute cache reads.

    Compare like target dates. Sunset SOC avoids interpreting normal depletion of
    today's remaining solar as forecast volatility. Future daily solar is also
    checked, including when battery saturation hides its effect on sunset SOC.
    """
    cutoff = (now - timedelta(hours=3)).isoformat()
    recent = [r for r in history if r["at"] >= cutoff]
    targets = {r["date"]: {"soc": r["sunset_soc_pct"], "solar": r["solar_kwh"]}
               for r in rows}
    unstable = False
    for old in recent:
        for day in targets.keys() & old["targets"].keys():
            a, b = targets[day], old["targets"][day]
            if abs(a["soc"] - b["soc"]) > 10:
                unstable = True
            if day > now.date().isoformat() and abs(a["solar"] - b["solar"]) > max(5, .25 * max(a["solar"], b["solar"])):
                unstable = True
    if revision and (not recent or recent[-1]["revision"] != revision or
                     recent[-1]["candidate"] != candidate or unstable):
        recent.append({"at": now.isoformat(), "revision": revision,
                       "targets": targets, "candidate": candidate})
    # A reversal on a cached refresh must invalidate previous confirmations too.
    streak = []
    seen = set()
    for sample in reversed(recent):
        if sample["candidate"] != candidate or not candidate:
            break
        if sample["revision"] not in seen:
            streak.append(sample)
            seen.add(sample["revision"])
    stable = (len(streak) >= 3 and
              (now - datetime.fromisoformat(streak[-1]["at"])).total_seconds() >= 3600)
    return recent[-240:], unstable, stable, len(streak)


def gate(
    *,
    fresh,
    unstable,
    confidence,
    candidate,
    stable,
    storm,
    storm_entity=None,
    storm_state=None,
):
    if not fresh:
        return "unavailable", "Forecast inputs are missing or stale—do not act."
    if storm is None:
        entity = storm_entity or "configured storm safety sensor"
        state = storm_state or "missing"
        return (
            "storm_sensor_unavailable",
            f"Storm safety sensor unavailable: {entity} = {state}—do not act.",
        )
    if storm is True:
        entity = storm_entity or "configured storm safety sensor"
        state = storm_state or "on"
        return (
            "storm_active",
            f"Storm protection is active: {entity} = {state}—do not act.",
        )
    if unstable:
        return "unstable", "Forecast unstable—do not act."
    if confidence in {"learning", "low"}:
        return confidence, "Forecast evidence is insufficient or inaccurate—do not act."
    if not candidate:
        return "clear", "Zero-export forecast does not currently require additional headroom."
    if not stable:
        return "pending", "Headroom risk is provisional; waiting for three refreshes over at least one hour."
    return "ready", "Export-defense headroom need is confirmed across repeated forecast refreshes."


def suppress_actions(data, status, reason):
    """Close every legacy action surface, retaining point forecasts for inspection."""
    data.update({
        "headroom_release": False,
        "today_recommended_presolar_discharge": 0.0,
        "recommended_overnight_discharge": 0.0,
        "today_strategy": "hold", "tomorrow_strategy": "hold",
        "today_strategy_reason": reason, "tomorrow_strategy_reason": reason,
        "ev_plan": reason, "rolling_ev_status": "hold",
        "rolling_ev_status_reason": reason, "rolling_ev_advisory_detail": status,
        "rolling_ev_auto_charge_eligible": False, "rolling_ev_auto_charge_reason": reason,
        "rolling_ev_recommended_energy_kwh": 0.0,
        "rolling_ev_window_start": None, "rolling_ev_window_end": None,
        "rolling_ev_window_solar_kwh": 0.0, "rolling_ev_window_grid_kwh": 0.0,
        "rolling_ev_window_solar_fraction_pct": None,
        "rolling_ev_headroom_preserved_kwh": 0.0,
        "authoritative_headroom_risk": False, "authoritative_headroom_status": status,
        "authoritative_headroom_action": reason,
        "rolling_dynamic_load_days_count": 0, "rolling_dynamic_load_risk_dates": [],
        "rolling_dynamic_load_total_kwh": 0.0, "rolling_dynamic_load_next_3d_kwh": 0.0,
        "rolling_dynamic_load_forecast_status": status,
    })
    for row in data.get("rolling_day_plans", []):
        row["nominal_dynamic_load_needed"] = row.get("dynamic_load_needed", False)
        row["nominal_dynamic_load_needed_kwh"] = row.get(
            "dynamic_load_needed_kwh",
            0.0,
        )
        row["dynamic_load_needed"] = False
        row["dynamic_load_needed_kwh"] = 0.0
        row["status"] = (row["confidence"] if status == "clear" and
                         row.get("confidence") in {"learning", "low"} else status)
