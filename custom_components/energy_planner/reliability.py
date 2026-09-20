"""Evidence and fail-closed decision policy; no Home Assistant dependencies."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def evidence(records, lead):
    errors = [number(r.get("error_soc")) for r in records if r.get("lead") == lead]
    errors = [abs(v) for v in errors if v is not None][-30:]
    # Engineering floor, not a claimed statistical confidence interval.
    width = max([10.0 + 3.0 * lead, *errors])
    mae = sum(errors) / len(errors) if errors else None
    confidence = "learning" if len(errors) < 3 else "low"
    if len(errors) >= 3 and mae <= 10:
        confidence = "medium"
    if len(errors) >= 10 and mae <= 5:
        confidence = "high"
    return {"samples": len(errors), "mae_soc": mae, "width_soc": width,
            "confidence": confidence}


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


def gate(*, fresh, unstable, confidence, candidate, stable, storm):
    if not fresh:
        return "unavailable", "Forecast inputs are missing or stale—do not act."
    if storm is not False:
        return "blocked", "Storm protection is active or unknown—do not act."
    if unstable:
        return "unstable", "Forecast unstable—do not act."
    if confidence in {"learning", "low"}:
        return confidence, "Forecast evidence is insufficient or inaccurate—do not act."
    if not candidate:
        return "clear", "No headroom action survives conservative assumptions and the safety margin."
    if not stable:
        return "pending", "Headroom risk is provisional; waiting for three refreshes over at least one hour."
    return "ready", "Headroom need survives conservative assumptions, safety margin, and repeated refreshes."


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
        row["dynamic_load_needed"] = False
        row["dynamic_load_needed_kwh"] = 0.0
        row["status"] = (row["confidence"] if status == "clear" and
                         row.get("confidence") in {"learning", "low"} else status)
