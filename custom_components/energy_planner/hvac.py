"""Read-only HVAC learning and anomaly detection, independent of Home Assistant.

Forecasts are experimental shadow estimates, never an automatic control input.
"""
from datetime import datetime
from math import isfinite
from statistics import median


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
        return [sum(values[i] for i in group) / len(group) for group in groups
                if all(values[i] is not None for i in group)]
    temps, humidity = means(temperatures), means(humidities)
    return {"temperature_c": sum(temps) / len(temps) if temps else None,
            "humidity": sum(humidity) / len(humidity) if humidity else None,
            "room_count": len(temps),
            "spread_c": max(temps) - min(temps) if temps else None}


def observe(memory, sample):
    """Only continuous five-minute observations train the model.

    A missing load is suspected after 10 minutes, poor response after 30.
    These are advisory engineering thresholds, not equipment diagnoses.
    """
    now = sample["at"]
    rows = memory.setdefault("samples", [])
    memory["samples"] = rows = [r for r in rows if 0 <= now - r["at"] <= 30 * 86400]
    previous = memory.get("previous")
    continuous = previous and 0 < now - previous["at"] <= 600
    action = sample.get("action")
    required = ("indoor_c", "target_c", "outdoor_c", "condenser_w", "blower_w", "humidity")
    valid = all(number(sample.get(key)) is not None for key in required)
    if valid:
        valid = (0 <= sample["humidity"] <= 100 and sample["blower_w"] >= 0
                 and sample["condenser_w"] >= 0)
    if not valid or action not in ("cooling", "heating", "idle", "off", "fan"):
        memory.pop("call", None)
        memory.pop("missing_since", None)
        memory["previous"] = None
        return {"status": "unavailable", "reason": "HVAC inputs missing or stale", "samples": len(rows)}
    if not continuous or previous["action"] != action or previous["target_c"] != sample["target_c"]:
        memory["call"] = {"at": now, "temperature": sample["indoor_c"]}
        memory.pop("missing_since", None)
    call = memory["call"]
    demand = (action == "cooling" and sample["indoor_c"] > sample["target_c"] + .5 or
              action == "heating" and sample["indoor_c"] < sample["target_c"] - .5)
    idle = [r["blower_w"] for r in rows if r["action"] in ("idle", "off")]
    standby = median(idle) if len(idle) >= 12 else None
    blower_floor = max(20, (standby or 0) + 15)
    missing = demand and (sample["blower_w"] < blower_floor or
                          action == "cooling" and sample["condenser_w"] < 100)
    if missing:
        memory.setdefault("missing_since", now)
    else:
        memory.pop("missing_since", None)
    progress = (call["temperature"] - sample["indoor_c"]) * (1 if action == "cooling" else -1)
    status, reason = "learning", "Collecting HVAC power and temperature response"
    if missing and now - memory["missing_since"] >= 600:
        status, reason = "suspected_fault", "Sustained unmet setpoint with missing HVAC circuit load"
    elif demand and now - call["at"] >= 1800 and progress < .2:
        status, reason = "suspected_fault", "Sustained unmet setpoint without expected temperature progress"
    # Do not train on missing load, catch-up demand, or an anomalous response.
    if continuous and not missing and not demand and status != "suspected_fault":
        if not rows or now - rows[-1]["at"] >= 300:
            row = dict(sample)
            row["response_c_per_hour"] = (sample["indoor_c"] - previous["indoor_c"]) * 3600 / (now - previous["at"])
            rows.append(row)
    memory["previous"] = dict(sample)
    signatures = {}
    for mode in ("cooling", "heating", "fan", "idle", "off"):
        selected = [r for r in rows if r["action"] == mode]
        signatures[mode] = {"samples": len(selected),
                            "power_w": median([r["blower_w"] + (r["condenser_w"] if mode == "cooling" else 0)
                                               for r in selected]) if selected else None,
                            "response_c_per_hour": median([r["response_c_per_hour"] for r in selected]) if selected else None}
    return {"status": status, "reason": reason, "samples": len(rows),
            "standby_w": standby, "signatures": signatures,
            "electrical_power_w": sample["blower_w"] + sample["condenser_w"],
            "thermostat_action": action, "unmet_setpoint": demand}


def hourly_forecast(rows, hours, *, mode, target_c, now):
    """Matched historical duty samples, not assumed equipment wattage.

    Reject unsupported conditions; bounds are empirical envelopes, not confidence
    intervals. Heating counts blower electricity, never inferred gas energy.
    """
    result = []
    if mode not in ("cool", "heat") or target_c is None:
        return result
    modes = {"cooling", "idle", "off"} if mode == "cool" else {"heating", "idle", "off"}
    for hour in hours:
        at, outdoor, humidity = hour.get("at"), hour.get("temperature_c"), hour.get("humidity")
        if any(number(v) is None for v in (at, outdoor, humidity)) or not now <= at <= now + 7 * 86400:
            continue
        matches = [r for r in rows if r.get("mode") == mode and r["action"] in modes
                   and abs(r["target_c"] - target_c) <= 1
                   and abs(r["outdoor_c"] - outdoor) <= 2
                   and number(r.get("outdoor_humidity")) is not None
                   and abs(r["outdoor_humidity"] - humidity) <= 15]
        days = {datetime.fromtimestamp(r["at"]).date() for r in matches}
        powers = [r["blower_w"] + (r["condenser_w"] if mode == "cool" else 0) for r in matches]
        supported = len(matches) >= 36 and len(days) >= 3
        result.append({"at": at, "status": "shadow" if supported else "insufficient_evidence",
                       "expected_w": sum(powers) / len(powers) if supported else None,
                       "low_w": min(powers) if supported else None,
                       "high_w": max(powers) if supported else None,
                       "samples": len(matches), "days": len(days)})
    return result
