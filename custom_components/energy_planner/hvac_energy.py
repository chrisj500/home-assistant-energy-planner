"""Observed HVAC electricity, with explicit coverage and no gap backfill."""
from datetime import datetime, time
from math import isfinite


def update_energy(memory, now, power):
    """Integrate adjacent valid samples, splitting at local midnight (DST safe)."""
    day = now.date().isoformat()
    midnight = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo).timestamp()
    at = now.timestamp()
    previous = memory.get("previous")
    if memory.get("day") != day:
        memory.clear()
        memory.update(day=day, kwh=0.0, covered_seconds=0.0)
    try:
        power = float(power)
        if not isfinite(power):
            power = None
    except (TypeError, ValueError):
        power = None
    if power is None or power < 0:
        memory.pop("previous", None)
    else:
        if previous and 0 < at - previous["at"] <= 300:
            start = max(previous["at"], midnight)
            seconds = at - start
            start_power = previous["power"] + (power - previous["power"]) * (
                (start - previous["at"]) / (at - previous["at"]))
            memory["kwh"] += (start_power + power) / 2 * seconds / 3600000
            memory["covered_seconds"] += seconds
        memory["previous"] = {"at": at, "power": power}
    elapsed = at - midnight
    return {"day": day, "covered_seconds": round(memory["covered_seconds"]),
            "coverage_percent": round(100 * memory["covered_seconds"] / elapsed, 1) if elapsed else 0,
            "partial": elapsed - memory["covered_seconds"] > 1,
            "method": "trapezoidal integration of measured condenser + blower power; gaps excluded",
            "gas_excluded": True}
