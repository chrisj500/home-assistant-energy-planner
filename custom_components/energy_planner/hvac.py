"""Read-only HVAC learning and anomaly detection, independent of Home Assistant.

Forecasts are experimental shadow estimates, never an automatic control input.
"""
from datetime import UTC, datetime
from math import isfinite
from statistics import median


HVAC_READY_SAMPLES = 36
HVAC_READY_DAYS = 3


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (ValueError, TypeError):
        return None


def celsius(value, unit):
    value = number(value)
    if value is None or unit not in ("°F", "°C"):
        return None
    result = (value - 32) / 1.8 if unit == "°F" else value
    return result if -50 <= result <= 65 else None


def room_summary(temperatures, humidities):
    """Two main-bedroom devices count as one physical room."""
    groups = ((0,), (1,), (2, 3))

    def means(values):
        return [
            sum(values[i] for i in group) / len(group)
            for group in groups
            if all(values[i] is not None for i in group)
        ]

    temps, humidity = means(temperatures), means(humidities)
    return {
        "temperature_c": sum(temps) / len(temps) if temps else None,
        "humidity": sum(humidity) / len(humidity) if humidity else None,
        "room_count": len(temps),
        "spread_c": max(temps) - min(temps) if temps else None,
    }


def _sample_day(row):
    day = row.get("day")
    if day:
        return str(day)
    try:
        return datetime.fromtimestamp(float(row["at"]), tz=UTC).date().isoformat()
    except (KeyError, TypeError, ValueError, OSError):
        return None


def learning_progress(rows):
    """Return explicit general HVAC baseline-learning progress."""
    samples = len(rows)
    days = len({day for row in rows if (day := _sample_day(row)) is not None})
    ready = samples >= HVAC_READY_SAMPLES and days >= HVAC_READY_DAYS
    sample_fraction = min(samples / HVAC_READY_SAMPLES, 1.0)
    day_fraction = min(days / HVAC_READY_DAYS, 1.0)
    return {
        "learning_ready": ready,
        "learning_samples": samples,
        "learning_samples_required": HVAC_READY_SAMPLES,
        "learning_days": days,
        "learning_days_required": HVAC_READY_DAYS,
        "learning_progress_pct": round(min(sample_fraction, day_fraction) * 100, 1),
    }


def observe(memory, sample):
    """Only continuous five-minute observations train the model.

    A missing load is suspected after 10 minutes, poor response after 30.
    These are advisory engineering thresholds, not equipment diagnoses.
    """
    now = sample["at"]
    rows = memory.setdefault("samples", [])
    memory["samples"] = rows = [
        r for r in rows if 0 <= now - r["at"] <= 30 * 86400
    ]
    previous = memory.get("previous")
    continuous = previous and 0 < now - previous["at"] <= 600
    action = sample.get("action")
    required = (
        "indoor_c",
        "target_c",
        "outdoor_c",
        "condenser_w",
        "blower_w",
        "humidity",
    )
    valid = all(number(sample.get(key)) is not None for key in required)
    if valid:
        valid = (
            0 <= sample["humidity"] <= 100
            and sample["blower_w"] >= 0
            and sample["condenser_w"] >= 0
        )
    if not valid or action not in ("cooling", "heating", "idle", "off", "fan"):
        memory.pop("call", None)
        memory.pop("missing_since", None)
        memory["previous"] = None
        progress = learning_progress(rows)
        return {
            "status": "unavailable",
            "reason": "HVAC inputs missing or unavailable",
            "samples": len(rows),
            **progress,
        }

    if (
        not continuous
        or previous["action"] != action
        or previous["target_c"] != sample["target_c"]
    ):
        memory["call"] = {"at": now, "temperature": sample["indoor_c"]}
        memory.pop("missing_since", None)

    call = memory["call"]
    demand = (
        action == "cooling" and sample["indoor_c"] > sample["target_c"] + 0.5
        or action == "heating" and sample["indoor_c"] < sample["target_c"] - 0.5
    )
    idle = [r["blower_w"] for r in rows if r["action"] in ("idle", "off")]
    standby = median(idle) if len(idle) >= 12 else None
    blower_floor = max(20, (standby or 0) + 15)
    missing = demand and (
        sample["blower_w"] < blower_floor
        or action == "cooling" and sample["condenser_w"] < 100
    )
    if missing:
        memory.setdefault("missing_since", now)
    else:
        memory.pop("missing_since", None)

    progress_c = (
        call["temperature"] - sample["indoor_c"]
    ) * (1 if action == "cooling" else -1)
    status = "learning"
    reason = "Collecting HVAC power and temperature response"

    if missing and now - memory["missing_since"] >= 600:
        status = "suspected_fault"
        reason = "Sustained unmet setpoint with missing HVAC circuit load"
    elif demand and now - call["at"] >= 1800 and progress_c < 0.2:
        status = "suspected_fault"
        reason = "Sustained unmet setpoint without expected temperature progress"

    # Do not train on missing load, catch-up demand, or an anomalous response.
    if continuous and not missing and not demand and status != "suspected_fault":
        if not rows or now - rows[-1]["at"] >= 300:
            row = dict(sample)
            row["response_c_per_hour"] = (
                (sample["indoor_c"] - previous["indoor_c"])
                * 3600
                / (now - previous["at"])
            )
            rows.append(row)

    memory["previous"] = dict(sample)
    learning = learning_progress(rows)
    if status == "learning":
        if learning["learning_ready"]:
            status = "ready"
            reason = (
                "HVAC baseline ready; hourly forecasts still require enough "
                "weather-matched observations for each hour"
            )
        else:
            reason = (
                "Collecting HVAC baseline: "
                f"{learning['learning_samples']}/{HVAC_READY_SAMPLES} clean "
                "five-minute samples across "
                f"{learning['learning_days']}/{HVAC_READY_DAYS} days"
            )

    signatures = {}
    for mode in ("cooling", "heating", "fan", "idle", "off"):
        selected = [r for r in rows if r["action"] == mode]
        signatures[mode] = {
            "samples": len(selected),
            "power_w": (
                median(
                    [
                        r["blower_w"]
                        + (r["condenser_w"] if mode == "cooling" else 0)
                        for r in selected
                    ]
                )
                if selected
                else None
            ),
            "response_c_per_hour": (
                median([r["response_c_per_hour"] for r in selected])
                if selected
                else None
            ),
        }

    return {
        "status": status,
        "reason": reason,
        "samples": len(rows),
        "standby_w": standby,
        "signatures": signatures,
        "electrical_power_w": sample["blower_w"] + sample["condenser_w"],
        "thermostat_action": action,
        "unmet_setpoint": demand,
        **learning,
    }


def hourly_forecast(rows, hours, *, mode, target_c, now):
    """Matched historical duty samples, not assumed equipment wattage.

    Reject unsupported conditions; bounds are empirical envelopes, not confidence
    intervals. Heating counts blower electricity, never inferred gas energy.
    """
    result = []
    if mode not in ("cool", "heat") or target_c is None:
        return result
    modes = (
        {"cooling", "idle", "off"}
        if mode == "cool"
        else {"heating", "idle", "off"}
    )
    for hour in hours:
        at, outdoor, humidity = (
            hour.get("at"),
            hour.get("temperature_c"),
            hour.get("humidity"),
        )
        if (
            any(number(v) is None for v in (at, outdoor, humidity))
            or not now <= at <= now + 7 * 86400
        ):
            continue
        matches = [
            r
            for r in rows
            if r.get("mode") == mode
            and r["action"] in modes
            and abs(r["target_c"] - target_c) <= 1
            and abs(r["outdoor_c"] - outdoor) <= 2
            and number(r.get("outdoor_humidity")) is not None
            and abs(r["outdoor_humidity"] - humidity) <= 15
        ]
        days = {day for r in matches if (day := _sample_day(r)) is not None}
        powers = [
            r["blower_w"] + (r["condenser_w"] if mode == "cool" else 0)
            for r in matches
        ]
        supported = len(matches) >= HVAC_READY_SAMPLES and len(days) >= HVAC_READY_DAYS
        result.append(
            {
                "at": at,
                "status": "shadow" if supported else "insufficient_evidence",
                "expected_w": sum(powers) / len(powers) if supported else None,
                "low_w": min(powers) if supported else None,
                "high_w": max(powers) if supported else None,
                "samples": len(matches),
                "days": len(days),
                "samples_required": HVAC_READY_SAMPLES,
                "days_required": HVAC_READY_DAYS,
            }
        )
    return result
