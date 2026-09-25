from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EnergyIncrement:
    charged_kwh: float
    discharged_kwh: float


def is_power_unit(unit: str | None) -> bool:
    """Return whether a Home Assistant unit represents electrical power."""
    return str(unit or "").strip().lower() in {"w", "kw", "mw"}


def normalize_power_w(value: float, unit: str | None) -> float:
    """Normalize common Home Assistant power units to watts."""
    power = float(value)
    normalized = str(unit or "W").strip().lower()
    if normalized == "kw":
        return power * 1000.0
    if normalized == "mw":
        return power * 1_000_000.0
    return power


def integrate_signed_power(
    previous_w: float,
    current_w: float,
    elapsed_seconds: float,
) -> EnergyIncrement:
    """Integrate signed power, preserving charge/discharge across zero crossings."""
    seconds = max(float(elapsed_seconds), 0.0)
    if seconds <= 0.0:
        return EnergyIncrement(0.0, 0.0)

    start = float(previous_w)
    end = float(current_w)

    if start >= 0.0 and end >= 0.0:
        charged_wh = (start + end) / 2.0 * seconds / 3600.0
        return EnergyIncrement(charged_wh / 1000.0, 0.0)

    if start <= 0.0 and end <= 0.0:
        discharged_wh = (abs(start) + abs(end)) / 2.0 * seconds / 3600.0
        return EnergyIncrement(0.0, discharged_wh / 1000.0)

    denominator = abs(start) + abs(end)
    if denominator <= 0.0:
        return EnergyIncrement(0.0, 0.0)

    first_seconds = seconds * abs(start) / denominator
    second_seconds = seconds - first_seconds

    if start > 0.0:
        charged_wh = 0.5 * start * first_seconds / 3600.0
        discharged_wh = 0.5 * abs(end) * second_seconds / 3600.0
    else:
        discharged_wh = 0.5 * abs(start) * first_seconds / 3600.0
        charged_wh = 0.5 * end * second_seconds / 3600.0

    return EnergyIncrement(charged_wh / 1000.0, discharged_wh / 1000.0)
