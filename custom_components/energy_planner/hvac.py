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


def _target_reached(action, indoor_c, target_c):
    indoor = number(indoor_c)
    target = number(target_c)
    if indoor is None or target is None:
        return False
    if action == "cooling":
        return indoor <= target + 0.05
    if action == "heating":
        return indoor >= target - 0.05
    return False


def _overshoot_c(action, temperature_c, target_c):
    temperature = number(temperature_c)
    target = number(target_c)
    if temperature is None or target is None:
        return None
    if action == "cooling":
        return max(0.0, target - temperature)
    if action == "heating":
        return max(0.0, temperature - target)
    return None


def _update_overrun_call(overrun, sample, movement_indoor_c, movement_source):
    thermostat_overshoot = _overshoot_c(
        overrun.get("action"), sample.get("indoor_c"), overrun.get("target_c")
    )
    precision_overshoot = _overshoot_c(
        overrun.get("action"), movement_indoor_c, overrun.get("target_c")
    )
    if thermostat_overshoot is not None:
        overrun["thermostat_overshoot_c"] = thermostat_overshoot
        overrun["peak_thermostat_overshoot_c"] = max(
            thermostat_overshoot,
            number(overrun.get("peak_thermostat_overshoot_c")) or 0.0,
        )
    if precision_overshoot is not None:
        overrun["precision_overshoot_c"] = precision_overshoot
        overrun["peak_precision_overshoot_c"] = max(
            precision_overshoot,
            number(overrun.get("peak_precision_overshoot_c")) or 0.0,
        )
    overrun["temperature_signal_source"] = movement_source
    overrun["last_at"] = sample["at"]


def _finish_overrun(overrun, ended_at):
    started_at = number(overrun.get("at"))
    if started_at is None or ended_at <= started_at:
        return None
    call_started_at = number(overrun.get("call_started_at"))
    return {
        "ended_at": ended_at,
        "action": overrun.get("action"),
        "target_c": number(overrun.get("target_c")),
        "duration_minutes": (ended_at - started_at) / 60,
        "recovery_duration_minutes": number(
            overrun.get("recovery_duration_minutes")
        ),
        "total_call_minutes": (
            (ended_at - call_started_at) / 60
            if call_started_at is not None and ended_at >= call_started_at
            else None
        ),
        "peak_thermostat_overshoot_c": number(
            overrun.get("peak_thermostat_overshoot_c")
        ) or 0.0,
        "peak_precision_overshoot_c": number(
            overrun.get("peak_precision_overshoot_c")
        ) or 0.0,
        "temperature_signal_source": overrun.get("temperature_signal_source"),
        "started_mid_overrun": bool(overrun.get("started_mid_overrun")),
    }


def _recovery_population(rows):
    clean = [row for row in rows if row.get("phase_end") == "target_reached"]
    if clean:
        return clean, "clean_target_cycles"
    return rows, "legacy_cycles"


def _recovery_duration_projection(row, error_c):
    """Project ETA from a clean target-ended duration observation."""
    duration = number(
        row.get("logical_duration_minutes", row.get("duration_minutes"))
    )
    start_error = number(row.get("start_error_c"))
    if (
        duration is None
        or duration <= 0
        or start_error is None
        or start_error <= 0.05
        or error_c <= 0
    ):
        return None
    return duration * error_c / start_error


def _recovery_cycle(call, sample):
    """Return a target-ended observation even when no precision rate is usable."""
    logical_duration = sample["at"] - call["at"]
    if logical_duration <= 0 or logical_duration > 4 * 3600:
        return None

    action = call["action"]
    target_c = number(call.get("target_c"))
    start_indoor_c = number(call.get("indoor_c"))
    if action == "cooling":
        start_error_c = (
            max(0.0, start_indoor_c - target_c)
            if start_indoor_c is not None and target_c is not None
            else None
        )
    elif action == "heating":
        start_error_c = (
            max(0.0, target_c - start_indoor_c)
            if start_indoor_c is not None and target_c is not None
            else None
        )
    else:
        start_error_c = None

    # Preserve thermostat-referenced outdoor matching independently of whether
    # the high-resolution movement is sufficient for a physical rate sample.
    rate_indoor = number(call.get("rate_indoor_c"))
    rate_outdoor = number(call.get("rate_outdoor_c"))
    current_indoor = number(sample.get("indoor_c"))
    current_outdoor = number(sample.get("outdoor_c"))
    if rate_indoor is None:
        rate_indoor = start_indoor_c
    if rate_outdoor is None:
        rate_outdoor = number(call.get("outdoor_c"))
    outdoor_delta_c = None
    if (
        rate_indoor is not None
        and rate_outdoor is not None
        and current_indoor is not None
        and current_outdoor is not None
    ):
        mean_indoor = (rate_indoor + current_indoor) / 2
        mean_outdoor = (rate_outdoor + current_outdoor) / 2
        outdoor_delta_c = abs(mean_indoor - mean_outdoor)

    movement_now, movement_source = _temperature_signal(sample)
    movement_start = number(
        call.get("rate_movement_indoor_c", call.get("movement_indoor_c"))
    )
    movement_start_source = call.get(
        "rate_movement_source", call.get("movement_source")
    )
    rate_at = number(call.get("rate_at"))
    if rate_at is None:
        rate_at = call["at"]
    rate_duration = sample["at"] - rate_at
    rate = None
    rate_progress_c = None
    rate_rejection_reason = None

    if rate_duration < 600:
        rate_rejection_reason = "rate_segment_too_short"
    elif rate_duration > 4 * 3600:
        rate_rejection_reason = "rate_segment_too_long"
    elif movement_start is None or movement_now is None:
        rate_rejection_reason = "temperature_signal_missing"
    elif movement_start_source not in (None, movement_source):
        rate_rejection_reason = "temperature_signal_changed"
    else:
        rate_progress_c = _directional_progress(
            action, movement_start, movement_now
        )
        if rate_progress_c < 0.15:
            rate_rejection_reason = "insufficient_precision_movement"
        else:
            candidate = rate_progress_c / (rate_duration / 3600)
            if 0.05 <= candidate <= 10:
                rate = candidate
            else:
                rate_rejection_reason = "rate_out_of_range"

    return {
        "ended_at": sample["at"],
        "action": action,
        "rate_c_per_hour": rate,
        "rate_sample_valid": rate is not None,
        "rate_rejection_reason": rate_rejection_reason,
        "rate_progress_c": rate_progress_c,
        "outdoor_delta_c": outdoor_delta_c,
        "duration_minutes": logical_duration / 60,
        "logical_duration_minutes": logical_duration / 60,
        "start_error_c": start_error_c,
        "duration_sample_valid": (
            start_error_c is not None and start_error_c > 0.05
        ),
        "temperature_signal_source": movement_source,
    }


def _target_cycle_exists(cycles, action, ended_at):
    return any(
        row.get("phase_end") == "target_reached"
        and row.get("action") == action
        and number(row.get("ended_at")) is not None
        and abs(row["ended_at"] - ended_at) <= 120
        for row in cycles
    )


def _backfill_target_cycle_from_overrun(cycles, overrun):
    """Retain v0.1.60 target durations that were dropped for low movement."""
    if not isinstance(overrun, dict) or overrun.get("started_mid_overrun"):
        return None
    duration = number(overrun.get("recovery_duration_minutes"))
    action = overrun.get("action")
    target_at = number(overrun.get("at"))
    if duration is None or duration <= 0 or action not in ("cooling", "heating"):
        return None
    if target_at is None:
        ended_at = number(overrun.get("ended_at"))
        overrun_duration = number(overrun.get("duration_minutes"))
        if ended_at is not None and overrun_duration is not None:
            target_at = ended_at - overrun_duration * 60
    if target_at is None or _target_cycle_exists(cycles, action, target_at):
        return None

    row = {
        "ended_at": target_at,
        "action": action,
        "rate_c_per_hour": number(overrun.get("recovery_rate_c_per_hour")),
        "rate_sample_valid": number(
            overrun.get("recovery_rate_c_per_hour")
        ) is not None,
        "rate_rejection_reason": (
            None
            if number(overrun.get("recovery_rate_c_per_hour")) is not None
            else "backfilled_duration_only"
        ),
        "outdoor_delta_c": number(
            overrun.get("recovery_outdoor_delta_c")
        ),
        "duration_minutes": duration,
        "logical_duration_minutes": duration,
        "start_error_c": number(overrun.get("recovery_start_error_c")),
        "duration_sample_valid": (
            number(overrun.get("recovery_start_error_c")) is not None
            and number(overrun.get("recovery_start_error_c")) > 0.05
        ),
        "temperature_signal_source": overrun.get(
            "temperature_signal_source"
        ),
        "phase_end": "target_reached",
        "clean_target_cycle": True,
        "backfilled_from_overrun": True,
    }
    cycles.append(row)
    return row


def update_recovery(memory, sample, continuous, rate_continuous=None):
    """Learn time-to-target separately from post-target equipment overrun."""
    if rate_continuous is None:
        rate_continuous = continuous
    now = sample["at"]
    cycles = memory.setdefault("recovery_cycles", [])
    memory["recovery_cycles"] = cycles = [
        row for row in cycles if 0 <= now - row.get("ended_at", now) <= 30 * 86400
    ]
    overruns = memory.setdefault("overrun_cycles", [])
    memory["overrun_cycles"] = overruns = [
        row for row in overruns if 0 <= now - row.get("ended_at", now) <= 30 * 86400
    ]

    for completed_overrun in overruns:
        _backfill_target_cycle_from_overrun(cycles, completed_overrun)
    _backfill_target_cycle_from_overrun(cycles, memory.get("overrun_call"))

    action = sample["action"]
    active = action in ("cooling", "heating")
    movement_indoor_c, movement_source = _temperature_signal(sample)

    call = memory.get("recovery_call")
    overrun = memory.get("overrun_call")

    # Close a post-target overrun only when the equipment stops, direction
    # changes, or the user changes the target. Do not bounce back into recovery
    # if a coarse thermostat reading jitters after target was already reached.
    if isinstance(overrun, dict):
        target_changed = (
            number(overrun.get("target_c")) is None
            or abs(overrun["target_c"] - sample["target_c"]) > 0.1
        )
        overrun_compatible = (
            active
            and overrun.get("action") == action
            and not target_changed
        )
        if not overrun_compatible:
            record = _finish_overrun(overrun, now)
            if record is not None:
                overruns.append(record)
            memory.pop("overrun_call", None)
            overrun = None

    # A recovery is complete only at the comfort target. Equipment shutdown
    # before target, action changes, or target changes are aborted recoveries,
    # not valid time-to-target training examples.
    if isinstance(call, dict):
        target_changed = (
            number(call.get("target_c")) is None
            or abs(call["target_c"] - sample["target_c"]) > 0.1
        )
        if (
            not continuous
            or not active
            or call.get("action") != action
            or target_changed
        ):
            reasons = memory.setdefault("recovery_aborted_reasons", {})
            reason = (
                "continuity_lost"
                if not continuous
                else "equipment_stopped_before_target"
                if not active
                else "action_changed"
                if call.get("action") != action
                else "target_changed"
            )
            reasons[reason] = int(reasons.get(reason, 0)) + 1
            memory.pop("recovery_call", None)
            call = None

    estimate_action = _requested_recovery_action(
        sample, action if active else None
    )
    error = 0.0
    if estimate_action == "cooling":
        error = max(0.0, sample["indoor_c"] - sample["target_c"])
    elif estimate_action == "heating":
        error = max(0.0, sample["target_c"] - sample["indoor_c"])
    demand_active = (
        estimate_action in ("cooling", "heating")
        and error > 0.05
    )

    # Do not start a fake recovery when the compressor is already running at or
    # beyond target. That is an overrun phase.
    if active and overrun is None and call is None and demand_active:
        call = {
            "at": now,
            "action": action,
            "indoor_c": sample["indoor_c"],
            "movement_indoor_c": movement_indoor_c,
            "movement_source": movement_source,
            "target_c": sample["target_c"],
            "outdoor_c": sample["outdoor_c"],
            "rate_at": now,
            "rate_indoor_c": sample["indoor_c"],
            "rate_outdoor_c": sample["outdoor_c"],
            "rate_movement_indoor_c": movement_indoor_c,
            "rate_movement_source": movement_source,
        }
        memory["recovery_call"] = call

    recovery_completed_duration = None
    recovery_completed_rate = None
    if (
        active
        and overrun is None
        and isinstance(call, dict)
        and _target_reached(action, sample["indoor_c"], sample["target_c"])
    ):
        cycle = _recovery_cycle(call, sample)
        if cycle is not None:
            cycle["phase_end"] = "target_reached"
            cycle["clean_target_cycle"] = True
            cycles.append(cycle)
            recovery_completed_rate = cycle["rate_c_per_hour"]
        recovery_completed_duration = max(0.0, (now - call["at"]) / 60)
        overrun = {
            "at": now,
            "last_at": now,
            "action": action,
            "target_c": sample["target_c"],
            "call_started_at": call["at"],
            "recovery_duration_minutes": recovery_completed_duration,
            "recovery_rate_c_per_hour": recovery_completed_rate,
            "recovery_start_error_c": (
                number(cycle.get("start_error_c"))
                if cycle is not None
                else None
            ),
            "recovery_outdoor_delta_c": (
                number(cycle.get("outdoor_delta_c"))
                if cycle is not None
                else None
            ),
            "started_mid_overrun": False,
        }
        _update_overrun_call(
            overrun, sample, movement_indoor_c, movement_source
        )
        memory["overrun_call"] = overrun
        memory.pop("recovery_call", None)
        memory.pop("recovery_eta", None)
        call = None
        demand_active = False

    if (
        active
        and overrun is None
        and call is None
        and not demand_active
    ):
        # We may attach while the thermostat is already in its post-target
        # control band. Track the overrun, but do not fabricate the missing
        # recovery duration.
        overrun = {
            "at": now,
            "last_at": now,
            "action": action,
            "target_c": sample["target_c"],
            "call_started_at": now,
            "recovery_duration_minutes": None,
            "recovery_rate_c_per_hour": None,
            "started_mid_overrun": True,
        }
        _update_overrun_call(
            overrun, sample, movement_indoor_c, movement_source
        )
        memory["overrun_call"] = overrun

    if isinstance(overrun, dict) and active:
        _update_overrun_call(
            overrun, sample, movement_indoor_c, movement_source
        )
        demand_active = False

    live_rate = None
    call_minutes = None
    rate_segment_minutes = None
    if active and isinstance(call, dict):
        rate_source = call.get(
            "rate_movement_source", call.get("movement_source")
        )
        if (
            rate_source is not None
            and movement_source is not None
            and rate_source != movement_source
        ):
            call.update(
                rate_at=now,
                rate_indoor_c=sample["indoor_c"],
                rate_outdoor_c=sample["outdoor_c"],
                rate_movement_indoor_c=movement_indoor_c,
                rate_movement_source=movement_source,
                temperature_signal_rebased_at=now,
            )
        logical_duration = now - call["at"]
        call_minutes = max(0.0, logical_duration / 60)
        rate_at = number(call.get("rate_at"))
        if rate_at is None:
            rate_at = call["at"]
        rate_duration = now - rate_at
        rate_segment_minutes = max(0.0, rate_duration / 60)
        call_source = call.get(
            "rate_movement_source", call.get("movement_source")
        )
        call_movement = number(
            call.get(
                "rate_movement_indoor_c",
                call.get("movement_indoor_c"),
            )
        )
        if (
            rate_continuous
            and rate_duration >= 600
            and call_movement is not None
            and movement_indoor_c is not None
            and call_source in (None, movement_source)
        ):
            progress = _directional_progress(
                action, call_movement, movement_indoor_c
            )
            if progress >= 0.15:
                candidate = progress / (rate_duration / 3600)
                if 0.05 <= candidate <= 10:
                    live_rate = candidate
    elif isinstance(overrun, dict):
        call_minutes = number(overrun.get("recovery_duration_minutes"))

    models = {}
    for recovery_action in ("cooling", "heating"):
        all_action_cycles = [
            row for row in cycles if row["action"] == recovery_action
        ]
        model_cycles, population = _recovery_population(all_action_cycles)
        count = len(model_cycles)
        rate_cycles = [
            row
            for row in model_cycles
            if number(row.get("rate_c_per_hour")) is not None
        ]
        duration_cycles = [
            row
            for row in model_cycles
            if number(row.get("start_error_c")) is not None
            and number(
                row.get(
                    "logical_duration_minutes",
                    row.get("duration_minutes"),
                )
            ) is not None
            and number(row.get("start_error_c")) > 0.05
        ]
        usable_eta_samples = max(len(rate_cycles), len(duration_cycles))
        confidence = _evidence_confidence(usable_eta_samples)
        models[recovery_action] = {
            "samples": count,
            "usable_eta_samples": usable_eta_samples,
            "rate_samples": len(rate_cycles),
            "duration_samples": len(duration_cycles),
            "all_samples": len(all_action_cycles),
            "clean_target_samples": len(
                [
                    row
                    for row in all_action_cycles
                    if row.get("phase_end") == "target_reached"
                ]
            ),
            "population": population,
            "rate_c_per_hour": (
                median([row["rate_c_per_hour"] for row in rate_cycles])
                if rate_cycles
                else None
            ),
            "duration_minutes_per_c": (
                median(
                    [
                        number(
                            row.get(
                                "logical_duration_minutes",
                                row.get("duration_minutes"),
                            )
                        )
                        / number(row.get("start_error_c"))
                        for row in duration_cycles
                    ]
                )
                if duration_cycles
                else None
            ),
            "confidence": confidence,
            "status": (
                "ready"
                if confidence == "high"
                else "provisional"
                if confidence in ("low", "medium")
                else "learning"
            ),
        }

    all_estimate_cycles = [
        row for row in cycles if row["action"] == estimate_action
    ]
    estimate_cycles, estimate_population = _recovery_population(
        all_estimate_cycles
    )
    current_delta = abs(sample["indoor_c"] - sample["outdoor_c"])
    matched = [
        row
        for row in estimate_cycles
        if number(row.get("outdoor_delta_c")) is not None
        and abs(row["outdoor_delta_c"] - current_delta) <= 4
    ]
    if estimate_action and not matched:
        matched = estimate_cycles

    rate_matched = [
        row
        for row in matched
        if number(row.get("rate_c_per_hour")) is not None
    ]
    historical_rate = (
        median([row["rate_c_per_hour"] for row in rate_matched])
        if rate_matched
        else None
    )
    duration_projections = [
        projected
        for row in matched
        for projected in [_recovery_duration_projection(row, error)]
        if projected is not None
    ]
    historical_duration_eta = (
        median(duration_projections) if duration_projections else None
    )
    historical_rate_confidence = _evidence_confidence(len(rate_matched))
    historical_duration_confidence = _evidence_confidence(
        len(duration_projections)
    )

    rate = live_rate if live_rate is not None else historical_rate
    if live_rate is not None:
        source = "live_call"
        raw_eta = (
            min(360.0, error / live_rate * 60)
            if demand_active and live_rate > 0
            else None
        )
        historical_confidence = (
            historical_duration_confidence
            if historical_duration_confidence != "none"
            else historical_rate_confidence
        )
    elif historical_duration_eta is not None:
        source = "history_duration"
        raw_eta = (
            min(360.0, historical_duration_eta)
            if demand_active
            else None
        )
        historical_confidence = historical_duration_confidence
    elif historical_rate is not None:
        source = "history"
        raw_eta = (
            min(360.0, error / historical_rate * 60)
            if demand_active and historical_rate > 0
            else None
        )
        historical_confidence = historical_rate_confidence
    else:
        source = "learning"
        raw_eta = None
        historical_confidence = "none"

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

    if isinstance(overrun, dict):
        memory.pop("recovery_eta", None)
        eta = 0.0
        eta_target_at = number(overrun.get("at"))
        source = "at_target"
        eta_correction_reason = "target_reached"
    elif not demand_active:
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
                and now - last_corrected
                >= RECOVERY_ETA_CORRECTION_INTERVAL_SECONDS
                and material_change
            )
            if expired or live_correction_due:
                eta_state["target_at"] = projected_target_at
                eta_state["last_corrected_at"] = now
                eta_state["correction_reason"] = (
                    "expired_reanchor"
                    if expired
                    else "live_rate_correction"
                )
        eta_target_at = number(eta_state.get("target_at"))
        eta_last_corrected_at = number(
            eta_state.get("last_corrected_at")
        )
        eta_correction_reason = eta_state.get("correction_reason")
        if eta_target_at is not None:
            eta = max(
                0.0,
                min(360.0, (eta_target_at - now) / 60),
            )

    recovery_source = (
        source if demand_active or source == "at_target" else "inactive"
    )
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
    elif recovery_source in ("history", "history_duration"):
        confidence = historical_confidence
        recovery_status = (
            "ready" if confidence == "high" else "provisional"
        )
    else:
        recovery_status = "learning"
        confidence = "none"

    overrun_models = {}
    for overrun_action in ("cooling", "heating"):
        selected = [
            row for row in overruns if row.get("action") == overrun_action
        ]
        count = len(selected)
        overrun_models[overrun_action] = {
            "samples": count,
            "duration_minutes": (
                median([row["duration_minutes"] for row in selected])
                if selected
                else None
            ),
            "thermostat_overshoot_c": (
                median(
                    [
                        row["peak_thermostat_overshoot_c"]
                        for row in selected
                    ]
                )
                if selected
                else None
            ),
            "precision_overshoot_c": (
                median(
                    [
                        row["peak_precision_overshoot_c"]
                        for row in selected
                    ]
                )
                if selected
                else None
            ),
            "confidence": _evidence_confidence(count),
        }

    overrun_active = isinstance(overrun, dict) and active
    overrun_duration = (
        max(0.0, (now - overrun["at"]) / 60)
        if overrun_active
        else None
    )
    equipment_call_started_at = (
        number(call.get("at"))
        if isinstance(call, dict)
        else number(overrun.get("call_started_at"))
        if isinstance(overrun, dict)
        else None
    )
    equipment_call_minutes = (
        max(0.0, (now - equipment_call_started_at) / 60)
        if active and equipment_call_started_at is not None
        else None
    )

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
        "eta_method": (
            "target_time_countdown"
            if eta_target_at is not None
            else None
        ),
        "eta_last_corrected_at": eta_last_corrected_at,
        "eta_correction_reason": eta_correction_reason,
        "rate_c_per_hour": rate,
        "source": recovery_source,
        "temperature_signal_source": movement_source,
        "temperature_signal_c": movement_indoor_c,
        "thermostat_temperature_c": sample["indoor_c"],
        "call_minutes": call_minutes,
        "equipment_call_minutes": equipment_call_minutes,
        "equipment_call_started_at": equipment_call_started_at,
        "target_reached_at": (
            number(overrun.get("at"))
            if isinstance(overrun, dict)
            else None
        ),
        "rate_segment_minutes": rate_segment_minutes,
        "completed_cycles": len(
            [
                row
                for row in cycles
                if row["action"] in ("cooling", "heating")
            ]
        ),
        "clean_target_cycles": len(
            [
                row
                for row in cycles
                if row.get("phase_end") == "target_reached"
            ]
        ),
        "matched_cycles": len(matched) if estimate_action else 0,
        "matched_rate_cycles": len(rate_matched) if estimate_action else 0,
        "matched_duration_cycles": (
            len(duration_projections) if estimate_action else 0
        ),
        "historical_duration_eta_minutes": historical_duration_eta,
        "model_population": estimate_population,
        "models": models,
        "aborted_calls": sum(
            int(value)
            for value in memory.get(
                "recovery_aborted_reasons", {}
            ).values()
        ),
        "aborted_reasons": dict(
            memory.get("recovery_aborted_reasons", {})
        ),
        "overrun": {
            "active": overrun_active,
            "duration_minutes": overrun_duration,
            "started_at": (
                number(overrun.get("at"))
                if isinstance(overrun, dict)
                else None
            ),
            "started_mid_overrun": (
                bool(overrun.get("started_mid_overrun"))
                if isinstance(overrun, dict)
                else False
            ),
            "thermostat_overshoot_c": (
                number(overrun.get("thermostat_overshoot_c"))
                if isinstance(overrun, dict)
                else None
            ),
            "precision_overshoot_c": (
                number(overrun.get("precision_overshoot_c"))
                if isinstance(overrun, dict)
                else None
            ),
            "peak_thermostat_overshoot_c": (
                number(overrun.get("peak_thermostat_overshoot_c"))
                if isinstance(overrun, dict)
                else None
            ),
            "peak_precision_overshoot_c": (
                number(overrun.get("peak_precision_overshoot_c"))
                if isinstance(overrun, dict)
                else None
            ),
            "recovery_duration_minutes": (
                number(overrun.get("recovery_duration_minutes"))
                if isinstance(overrun, dict)
                else recovery_completed_duration
            ),
            "recovery_rate_c_per_hour": (
                number(overrun.get("recovery_rate_c_per_hour"))
                if isinstance(overrun, dict)
                else recovery_completed_rate
            ),
            "completed_cycles": len(overruns),
            "models": overrun_models,
        },
        "restart_interrupted_cycles": int(
            memory.get("recovery_restart_interruptions", 0)
        ),
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


def _restart_resume(memory, sample, previous, continuous):
    """Validate persisted transient state on the first observation after restart."""
    if not memory.pop("_restart_pending", False):
        return bool(continuous), bool(continuous)

    now = sample["at"]
    previous_at = (
        number(previous.get("at"))
        if isinstance(previous, dict)
        else None
    )
    gap = now - previous_at if previous_at is not None else None
    previous_target = (
        number(previous.get("target_c"))
        if isinstance(previous, dict)
        else None
    )
    current_target = number(sample.get("target_c"))
    same_target = (
        previous_target is not None
        and current_target is not None
        and abs(previous_target - current_target) <= 0.1
    )
    same_action = (
        isinstance(previous, dict)
        and previous.get("action") == sample.get("action")
    )
    same_mode = (
        isinstance(previous, dict)
        and previous.get("mode") == sample.get("mode")
    )
    compatible = (
        gap is not None
        and gap > 0
        and same_target
        and same_action
        and same_mode
    )

    had_recovery = isinstance(memory.get("recovery_call"), dict)
    had_overrun = isinstance(memory.get("overrun_call"), dict)
    had_thermal = isinstance(memory.get("thermal_window"), dict)

    if not compatible:
        for key in (
            "call",
            "missing_since",
            "recovery_call",
            "overrun_call",
            "thermal_window",
        ):
            memory.pop(key, None)
        memory["_restart_resume"] = {
            "status": "state_changed_or_missing_baseline",
            "gap_minutes": (
                gap / 60 if gap is not None and gap >= 0 else None
            ),
            "resumed_recovery_call": False,
            "resumed_overrun_call": False,
            "resumed_thermal_window": False,
            "rate_learning_rebased": False,
        }
        return bool(continuous), bool(continuous)

    if gap <= 600:
        memory["_restart_resume"] = {
            "status": "resumed_full_continuity",
            "gap_minutes": gap / 60,
            "resumed_recovery_call": had_recovery,
            "resumed_overrun_call": had_overrun,
            "resumed_thermal_window": had_thermal,
            "rate_learning_rebased": False,
        }
        return True, True

    recovery_call = memory.get("recovery_call")
    rate_rebased = isinstance(recovery_call, dict)
    if rate_rebased:
        movement_indoor_c, movement_source = _temperature_signal(sample)
        recovery_call.update(
            rate_at=now,
            rate_indoor_c=sample["indoor_c"],
            rate_outdoor_c=sample["outdoor_c"],
            rate_movement_indoor_c=movement_indoor_c,
            rate_movement_source=movement_source,
            restart_rate_rebased_at=now,
        )
    memory.pop("thermal_window", None)
    memory.pop("call", None)
    memory.pop("missing_since", None)
    memory["_restart_resume"] = {
        "status": "resumed_logical_rebased_measurements",
        "gap_minutes": gap / 60,
        "resumed_recovery_call": had_recovery,
        "resumed_overrun_call": had_overrun,
        "resumed_thermal_window": False,
        "rate_learning_rebased": rate_rebased,
    }
    return True, False


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
    continuous = bool(previous and 0 < now - previous["at"] <= 600)
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
        memory.pop("overrun_call", None)
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

    logical_continuous, measurement_continuous = _restart_resume(
        memory, sample, previous, continuous
    )
    recovery = update_recovery(
        memory,
        sample,
        logical_continuous,
        rate_continuous=measurement_continuous,
    )
    thermal = update_thermal_model(memory, sample, measurement_continuous)
    movement_indoor_c, movement_source = _temperature_signal(sample)

    if (
        not measurement_continuous
        or not isinstance(memory.get("call"), dict)
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
    if measurement_continuous and not missing and not demand and status != "suspected_fault":
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
        "restart_resume": memory.get("_restart_resume"),
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
