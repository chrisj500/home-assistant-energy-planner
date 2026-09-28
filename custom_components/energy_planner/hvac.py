"""Read-only HVAC learning and anomaly detection, independent of Home Assistant.

Forecasts are experimental shadow estimates, never an automatic control input.
"""
from datetime import UTC, datetime
from math import isfinite
from statistics import median


HVAC_READY_SAMPLES = 36
HVAC_READY_DAYS = 3

RECOVERY_ETA_CORRECTION_INTERVAL_SECONDS = 300
RECOVERY_ETA_MATERIAL_MINUTES = 5.0
RECOVERY_ETA_MATERIAL_FRACTION = 0.20


VALID_ACTIONS = ("cooling", "heating", "idle", "off", "fan")
CONDENSER_RUNNING_W = 100
BLOWER_RUNNING_W = 20


def effective_action(mode, reported_action, condenser_w, blower_w):
    """Return a conservative HVAC action and its source.

    Prefer the thermostat's explicit hvac_action. Some climate integrations
    expose only the HVAC mode; for those, infer only states supported by the
    measured circuit loads. This avoids treating mode=cool as proof that the
    compressor is running.
    """
    if reported_action in VALID_ACTIONS:
        return reported_action, "thermostat"

    mode = str(mode or "").lower()
    condenser = number(condenser_w)
    blower = number(blower_w)

    if mode == "off":
        return "off", "inferred_mode"

    if condenser is None or blower is None:
        return None, "insufficient_power"

    if mode in ("cool", "dry"):
        if condenser >= CONDENSER_RUNNING_W:
            return "cooling", "inferred_power"
        if blower >= BLOWER_RUNNING_W:
            return "fan", "inferred_power"
        return "idle", "inferred_power"

    if mode == "heat":
        if blower >= BLOWER_RUNNING_W:
            return "heating", "inferred_power"
        return "idle", "inferred_power"

    if mode == "fan_only":
        if blower >= BLOWER_RUNNING_W:
            return "fan", "inferred_power"
        return "idle", "inferred_power"

    # Auto/heat-cool without an explicit thermostat action is ambiguous for gas
    # heat when only blower/control power is measured. Refuse to invent one.
    if mode == "heat_cool" and condenser >= CONDENSER_RUNNING_W:
        return "cooling", "inferred_power"

    return None, "unsupported_mode"


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
        "precision_temperature_c": median(temps) if temps else None,
        "physical_temperatures_c": temps,
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


def _directional_progress(action, start_c, end_c):
    if action == "cooling":
        return start_c - end_c
    if action == "heating":
        return end_c - start_c
    return 0.0


def _temperature_signal(sample):
    """Use high-resolution room movement when available; thermostat remains control truth."""
    precision = number(sample.get("precision_indoor_c"))
    if precision is not None:
        return precision, str(
            sample.get("precision_temperature_source") or "homepod_physical_room_median"
        )
    return number(sample.get("indoor_c")), "thermostat"


def _evidence_confidence(samples):
    """Consumer-style confidence: estimate early, strengthen with evidence."""
    if samples <= 0:
        return "none"
    if samples == 1:
        return "low"
    if samples == 2:
        return "medium"
    return "high"


def _requested_recovery_action(sample, active_action):
    """Return the recovery direction implied by mode/setpoint even before HVAC starts."""
    if active_action in ("cooling", "heating"):
        return active_action

    mode = str(sample.get("mode") or "").lower()
    indoor = number(sample.get("indoor_c"))
    target = number(sample.get("target_c"))
    if indoor is None or target is None:
        return None

    if mode in ("cool", "dry") and indoor > target + 0.05:
        return "cooling"
    if mode == "heat" and indoor < target - 0.05:
        return "heating"
    return None


def _recovery_cycle(call, sample):
    duration = sample["at"] - call["at"]
    if duration < 600 or duration > 4 * 3600:
        return None
    movement_now, movement_source = _temperature_signal(sample)
    movement_start = number(call.get("movement_indoor_c"))
    if movement_start is None or movement_now is None:
        return None
    if call.get("movement_source") not in (None, movement_source):
        return None
    progress = _directional_progress(
        call["action"], movement_start, movement_now
    )
    if progress < 0.15:
        return None
    rate = progress / (duration / 3600)
    if not 0.05 <= rate <= 10:
        return None
    # Preserve thermostat-referenced outdoor matching so new cycles remain
    # comparable with the existing historical recovery-cycle population.
    mean_indoor = (call["indoor_c"] + sample["indoor_c"]) / 2
    mean_outdoor = (call["outdoor_c"] + sample["outdoor_c"]) / 2
    return {
        "ended_at": sample["at"],
        "action": call["action"],
        "rate_c_per_hour": rate,
        "outdoor_delta_c": abs(mean_indoor - mean_outdoor),
        "duration_minutes": duration / 60,
        "temperature_signal_source": movement_source,
    }


def update_recovery(memory, sample, continuous):
    """Learn recovery rate and maintain a Nest-like countdown to the target."""
    now = sample["at"]
    cycles = memory.setdefault("recovery_cycles", [])
    memory["recovery_cycles"] = cycles = [
        row for row in cycles if 0 <= now - row.get("ended_at", now) <= 30 * 86400
    ]

    action = sample["action"]
    active = action in ("cooling", "heating")
    call = memory.get("recovery_call")

    if call is not None:
        target_changed = abs(call["target_c"] - sample["target_c"]) > 0.1
        if (
            not continuous
            or not active
            or call["action"] != action
            or target_changed
        ):
            if continuous:
                cycle = _recovery_cycle(call, sample)
                if cycle is not None:
                    cycles.append(cycle)
            memory.pop("recovery_call", None)
            call = None

    if active and call is None:
        movement_indoor_c, movement_source = _temperature_signal(sample)
        call = {
            "at": now,
            "action": action,
            "indoor_c": sample["indoor_c"],
            "movement_indoor_c": movement_indoor_c,
            "movement_source": movement_source,
            "target_c": sample["target_c"],
            "outdoor_c": sample["outdoor_c"],
        }
        memory["recovery_call"] = call

    estimate_action = _requested_recovery_action(sample, action if active else None)
    error = 0.0
    if estimate_action == "cooling":
        error = max(0.0, sample["indoor_c"] - sample["target_c"])
    elif estimate_action == "heating":
        error = max(0.0, sample["target_c"] - sample["indoor_c"])

    movement_indoor_c, movement_source = _temperature_signal(sample)
    live_rate = None
    call_minutes = None
    if active and call is not None:
        duration = now - call["at"]
        call_minutes = max(0.0, duration / 60)
        call_source = call.get("movement_source")
        call_movement = number(call.get("movement_indoor_c"))
        if (
            duration >= 600
            and call_movement is not None
            and movement_indoor_c is not None
            and call_source in (None, movement_source)
        ):
            progress = _directional_progress(
                action, call_movement, movement_indoor_c
            )
            if progress >= 0.15:
                candidate = progress / (duration / 3600)
                if 0.05 <= candidate <= 10:
                    live_rate = candidate

    models = {}
    for recovery_action in ("cooling", "heating"):
        action_cycles = [row for row in cycles if row["action"] == recovery_action]
        count = len(action_cycles)
        confidence = _evidence_confidence(count)
        models[recovery_action] = {
            "samples": count,
            "rate_c_per_hour": (
                median([row["rate_c_per_hour"] for row in action_cycles])
                if action_cycles
                else None
            ),
            "confidence": confidence,
            "status": (
                "ready" if confidence == "high"
                else "provisional" if confidence in ("low", "medium")
                else "learning"
            ),
        }

    current_delta = abs(sample["indoor_c"] - sample["outdoor_c"])
    matched = [
        row
        for row in cycles
        if row["action"] == estimate_action
        and abs(row["outdoor_delta_c"] - current_delta) <= 4
    ]
    if estimate_action and not matched:
        matched = [row for row in cycles if row["action"] == estimate_action]
    historical_rate = (
        median([row["rate_c_per_hour"] for row in matched])
        if matched
        else None
    )
    historical_confidence = _evidence_confidence(len(matched))

    rate = live_rate if live_rate is not None else historical_rate
    source = (
        "live_call"
        if live_rate is not None
        else "history"
        if historical_rate is not None
        else "learning"
    )

    demand_active = estimate_action in ("cooling", "heating") and error > 0.05
    raw_eta = (
        min(360.0, error / rate * 60)
        if demand_active and rate is not None and rate > 0
        else None
    )

    eta = None
    eta_target_at = None
    eta_last_corrected_at = None
    eta_correction_reason = None
    eta_state = memory.get("recovery_eta")
    same_eta = (
        isinstance(eta_state, dict)
        and eta_state.get("action") == estimate_action
        and number(eta_state.get("target_c")) is not None
        and abs(eta_state["target_c"] - sample["target_c"]) <= 0.1
    )

    if not demand_active:
        memory.pop("recovery_eta", None)
        if active and error <= 0.05:
            eta = 0.0
            eta_target_at = now
            source = "at_target"
            eta_correction_reason = "at_target"
    elif raw_eta is not None:
        projected_target_at = now + raw_eta * 60

        if not same_eta:
            eta_state = {
                "action": estimate_action,
                "target_c": sample["target_c"],
                "target_at": projected_target_at,
                "anchored_at": now,
                "last_corrected_at": now,
                "correction_reason": "initial_estimate",
            }
            memory["recovery_eta"] = eta_state
        else:
            target_at = number(eta_state.get("target_at"))
            if target_at is None:
                target_at = projected_target_at
                eta_state["target_at"] = target_at

            remaining = max(0.0, (target_at - now) / 60)
            last_corrected = number(eta_state.get("last_corrected_at"))
            if last_corrected is None:
                last_corrected = number(eta_state.get("anchored_at")) or now

            material_change = abs(raw_eta - remaining) >= max(
                RECOVERY_ETA_MATERIAL_MINUTES,
                max(remaining, 1.0) * RECOVERY_ETA_MATERIAL_FRACTION,
            )
            expired = target_at <= now
            live_correction_due = (
                live_rate is not None
                and now - last_corrected >= RECOVERY_ETA_CORRECTION_INTERVAL_SECONDS
                and material_change
            )

            if expired or live_correction_due:
                eta_state["target_at"] = projected_target_at
                eta_state["last_corrected_at"] = now
                eta_state["correction_reason"] = (
                    "expired_reanchor" if expired else "live_rate_correction"
                )

        eta_target_at = number(eta_state.get("target_at"))
        eta_last_corrected_at = number(eta_state.get("last_corrected_at"))
        eta_correction_reason = eta_state.get("correction_reason")
        if eta_target_at is not None:
            eta = max(0.0, min(360.0, (eta_target_at - now) / 60))

    recovery_source = source if demand_active or source == "at_target" else "inactive"
    if recovery_source == "inactive":
        recovery_status = "inactive"
        confidence = "none"
    elif recovery_source == "at_target":
        recovery_status = "ready"
        confidence = historical_confidence
    elif recovery_source == "live_call":
        recovery_status = "provisional"
        confidence = (
            historical_confidence
            if historical_confidence != "none"
            else "low"
        )
    elif recovery_source == "history":
        confidence = historical_confidence
        recovery_status = "ready" if confidence == "high" else "provisional"
    else:
        recovery_status = "learning"
        confidence = "none"

    return {
        "active": active,
        "demand_active": demand_active,
        "action": action if active else None,
        "requested_action": estimate_action,
        "status": recovery_status,
        "confidence": confidence,
        "eta_minutes": eta,
        "eta_raw_minutes": raw_eta,
        "eta_target_at": eta_target_at,
        "eta_method": "target_time_countdown" if eta_target_at is not None else None,
        "eta_last_corrected_at": eta_last_corrected_at,
        "eta_correction_reason": eta_correction_reason,
        "rate_c_per_hour": rate,
        "source": recovery_source,
        "temperature_signal_source": movement_source,
        "temperature_signal_c": movement_indoor_c,
        "thermostat_temperature_c": sample["indoor_c"],
        "call_minutes": call_minutes,
        "completed_cycles": len(
            [row for row in cycles if row["action"] in ("cooling", "heating")]
        ),
        "matched_cycles": len(matched) if estimate_action else 0,
        "models": models,
        "restart_interrupted_cycles": int(memory.get("recovery_restart_interruptions", 0)),
    }


def _thermal_candidate_detail(window):
    duration = window["last_at"] - window["at"]
    if duration < 1800:
        return None, "duration_too_short"
    if duration > 6 * 3600:
        return None, "duration_too_long"
    movement = window["last_indoor_c"] - window["indoor_c"]
    if abs(movement) < 0.15:
        return None, "insufficient_indoor_movement"
    hours = duration / 3600
    avg_outdoor = window["outdoor_sum"] / window["outdoor_count"]
    avg_indoor = (window["indoor_c"] + window["last_indoor_c"]) / 2
    delta = avg_indoor - avg_outdoor
    if abs(delta) < 2:
        return None, "small_indoor_outdoor_delta"
    drift = movement / hours
    if drift * delta >= 0:
        return None, "wrong_drift_direction"
    coefficient = -drift / delta
    if not 0.001 <= coefficient <= 1.5:
        return None, "coefficient_out_of_range"
    return {
        "coefficient_per_hour": coefficient,
        "observed_drift_c_per_hour": drift,
        "duration_minutes": duration / 60,
        "mean_outdoor_delta_c": abs(delta),
    }, None


def _thermal_candidate(window):
    candidate, _ = _thermal_candidate_detail(window)
    return candidate


def update_thermal_model(memory, sample, continuous):
    """Estimate passive building thermal decay from HVAC-off/idle windows."""
    now = sample["at"]
    history = memory.setdefault("thermal_samples", [])
    memory["thermal_samples"] = history = [
        row for row in history if 0 <= now - row.get("ended_at", now) <= 30 * 86400
    ]
    rejections = memory.setdefault("thermal_rejections", {})
    if not isinstance(rejections, dict):
        rejections = {}
        memory["thermal_rejections"] = rejections

    passive = (
        sample["action"] in ("idle", "off")
        and sample["condenser_w"] < CONDENSER_RUNNING_W
        and sample["blower_w"] < BLOWER_RUNNING_W
    )
    thermal_indoor_c, thermal_source = _temperature_signal(sample)
    window = memory.get("thermal_window")
    signal_changed = (
        window is not None
        and window.get("temperature_source") not in (None, thermal_source)
    )

    if window is not None and (not continuous or not passive or signal_changed):
        candidate, rejection = _thermal_candidate_detail(window)
        if candidate is not None:
            history.append({"ended_at": window["last_at"], **candidate})
        elif rejection is not None:
            rejections[rejection] = int(rejections.get(rejection, 0)) + 1
            memory["thermal_last_rejection"] = {
                "reason": rejection,
                "at": window["last_at"],
                "duration_minutes": max(
                    0.0, (window["last_at"] - window["at"]) / 60
                ),
            }
        memory.pop("thermal_window", None)
        window = None

    if passive:
        if window is None or not continuous:
            window = {
                "at": now,
                "indoor_c": thermal_indoor_c,
                "last_at": now,
                "last_indoor_c": thermal_indoor_c,
                "temperature_source": thermal_source,
                "outdoor_sum": sample["outdoor_c"],
                "outdoor_count": 1,
            }
            memory["thermal_window"] = window
        else:
            window["last_at"] = now
            window["last_indoor_c"] = thermal_indoor_c
            window["outdoor_sum"] += sample["outdoor_c"]
            window["outdoor_count"] += 1

    live = _thermal_candidate(window) if window is not None else None
    coefficients = [row["coefficient_per_hour"] for row in history]
    coefficient = (
        live["coefficient_per_hour"]
        if live is not None
        else median(coefficients)
        if coefficients
        else None
    )
    source = (
        "live_window"
        if live is not None
        else "history"
        if coefficients
        else "learning"
    )
    confidence = _evidence_confidence(len(coefficients))
    if live is not None and confidence == "none":
        confidence = "low"
    drift = (
        -coefficient * (thermal_indoor_c - sample["outdoor_c"])
        if coefficient is not None and thermal_indoor_c is not None
        else None
    )
    return {
        "status": (
            "ready"
            if source == "history" and confidence == "high"
            else "provisional"
            if coefficient is not None
            else "learning"
        ),
        "confidence": confidence,
        "source": source,
        "samples": len(history),
        "samples_required": 1,
        "samples_required_for_ready": 3,
        "coefficient_per_hour": coefficient,
        "time_constant_hours": 1 / coefficient if coefficient else None,
        "predicted_drift_c_per_hour": drift,
        "current_indoor_outdoor_delta_c": (
            thermal_indoor_c - sample["outdoor_c"]
            if thermal_indoor_c is not None
            else None
        ),
        "temperature_signal_source": thermal_source,
        "temperature_signal_c": thermal_indoor_c,
        "thermostat_temperature_c": sample["indoor_c"],
        "live_window_minutes": (
            (window["last_at"] - window["at"]) / 60 if window is not None else 0
        ),
        "rejected_windows": sum(int(value) for value in rejections.values()),
        "rejection_counts": dict(rejections),
        "last_rejection": memory.get("thermal_last_rejection"),
        "restart_interrupted_windows": int(rejections.get("restart_interrupted", 0)),
    }


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
    missing_inputs = [
        key for key in required if number(sample.get(key)) is None
    ]
    valid = not missing_inputs
    if valid:
        valid = (
            0 <= sample["humidity"] <= 100
            and sample["blower_w"] >= 0
            and sample["condenser_w"] >= 0
        )
    if not valid or action not in VALID_ACTIONS:
        memory.pop("call", None)
        memory.pop("missing_since", None)
        memory.pop("recovery_call", None)
        memory.pop("thermal_window", None)
        memory["previous"] = None
        progress = learning_progress(rows)
        issues = list(missing_inputs)
        if valid and action not in VALID_ACTIONS:
            issues.append("hvac_action")
        reason = (
            "HVAC inputs unavailable: " + ", ".join(issues)
            if issues
            else "HVAC inputs invalid"
        )
        return {
            "status": "unavailable",
            "reason": reason,
            "input_issues": issues,
            "samples": len(rows),
            **progress,
        }

    recovery = update_recovery(memory, sample, bool(continuous))
    thermal = update_thermal_model(memory, sample, bool(continuous))
    movement_indoor_c, movement_source = _temperature_signal(sample)

    if (
        not continuous
        or previous["action"] != action
        or previous["target_c"] != sample["target_c"]
        or memory.get("call", {}).get("temperature_source") not in (None, movement_source)
    ):
        memory["call"] = {
            "at": now,
            "temperature": movement_indoor_c,
            "temperature_source": movement_source,
        }
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
        call["temperature"] - movement_indoor_c
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
            previous_movement_c, previous_movement_source = _temperature_signal(previous)
            if previous_movement_source == movement_source:
                response_delta = movement_indoor_c - previous_movement_c
                response_source = movement_source
            else:
                response_delta = sample["indoor_c"] - previous["indoor_c"]
                response_source = "thermostat_fallback"
            row["response_c_per_hour"] = (
                response_delta
                * 3600
                / (now - previous["at"])
            )
            row["response_temperature_source"] = response_source
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
        "temperature_signal": {
            "source": movement_source,
            "temperature_c": movement_indoor_c,
            "thermostat_temperature_c": sample["indoor_c"],
            "precision_temperature_c": number(sample.get("precision_indoor_c")),
        },
        "recovery": recovery,
        "thermal": thermal,
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
