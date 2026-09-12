from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SunsetProjection:
    projected_soc: float
    projected_stored_kwh: float
    projected_charge_kwh: float
    projected_captured_ac_kwh: float
    projected_available_ac_kwh: float
    hours_to_sunset: float
    average_load_kw: float
    modeled_solar_start_kw: float
    model: str


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _solar_power_kw(
    elapsed_h: float,
    *,
    duration_h: float,
    current_solar_kw: float,
    remaining_solar_kwh: float,
    hours_to_peak: float | None,
) -> tuple[float, float, str]:
    """Return modeled solar power plus modeled start power and model label.

    The curve is energy-conserving: its integral from now to sunset equals
    ``remaining_solar_kwh``. After today's forecast peak, use a linear decay
    to zero at sunset. Before the peak, use a two-segment linear curve from
    current production to a solved peak and then to zero at sunset.
    """
    if duration_h <= 0 or remaining_solar_kwh <= 0:
        return 0.0, 0.0, "none"

    elapsed_h = _clamp(elapsed_h, 0.0, duration_h)
    current_solar_kw = max(current_solar_kw, 0.0)

    if hours_to_peak is not None and 0.05 < hours_to_peak < duration_h:
        t_peak = hours_to_peak
        # E = .5 * p0 * t_peak + .5 * p_peak * duration
        solved_peak = (2.0 * remaining_solar_kwh - current_solar_kw * t_peak) / duration_h
        peak_kw = max(solved_peak, current_solar_kw, 0.0)

        # If clamping the peak above the solved value would materially overstate
        # remaining energy, fall back to a normalized triangle.
        modeled_energy = 0.5 * current_solar_kw * t_peak + 0.5 * peak_kw * duration_h
        if modeled_energy > remaining_solar_kwh * 1.15:
            start_kw = 2.0 * remaining_solar_kwh / duration_h
            power = max(start_kw * (1.0 - elapsed_h / duration_h), 0.0)
            return power, start_kw, "post_peak_triangle_fallback"

        if elapsed_h <= t_peak:
            fraction = elapsed_h / t_peak
            power = current_solar_kw + (peak_kw - current_solar_kw) * fraction
        else:
            tail_h = duration_h - t_peak
            fraction = (elapsed_h - t_peak) / tail_h if tail_h > 0 else 1.0
            power = peak_kw * (1.0 - fraction)
        return max(power, 0.0), current_solar_kw, "pre_peak_piecewise_linear"

    start_kw = 2.0 * remaining_solar_kwh / duration_h
    power = max(start_kw * (1.0 - elapsed_h / duration_h), 0.0)
    return power, start_kw, "post_peak_linear"


def project_sunset_soc(
    *,
    now: datetime,
    sunset: datetime,
    current_soc_pct: float,
    capacity_kwh: float,
    charge_limit_pct: float,
    remaining_solar_kwh: float,
    expected_load_remaining_kwh: float,
    current_solar_w: float,
    peak_time: datetime | None,
    preferred_import_w: float = 250.0,
    harvest_capture_factor: float = 0.88,
    charge_efficiency: float = 0.90,
    step_minutes: int = 5,
) -> SunsetProjection:
    """Project battery SOC at sunset without daytime battery discharge.

    House-load deficits during the protected solar window are assigned to grid
    import, never to battery discharge. Only modeled battery-eligible solar
    charging can raise SOC.
    """
    capacity_kwh = max(float(capacity_kwh), 0.001)
    current_soc_pct = _clamp(float(current_soc_pct), 0.0, 100.0)
    charge_limit_pct = _clamp(float(charge_limit_pct), 0.0, 100.0)
    remaining_solar_kwh = max(float(remaining_solar_kwh), 0.0)
    expected_load_remaining_kwh = max(float(expected_load_remaining_kwh), 0.0)
    preferred_import_kw = max(float(preferred_import_w), 0.0) / 1000.0
    harvest_capture_factor = _clamp(float(harvest_capture_factor), 0.0, 1.0)
    charge_efficiency = _clamp(float(charge_efficiency), 0.0, 1.0)
    step_minutes = max(int(step_minutes), 1)

    duration_h = max((sunset - now).total_seconds() / 3600.0, 0.0)
    stored_kwh = capacity_kwh * current_soc_pct / 100.0
    charge_ceiling_kwh = capacity_kwh * charge_limit_pct / 100.0

    if duration_h <= 0 or stored_kwh >= charge_ceiling_kwh:
        projected_stored = min(stored_kwh, charge_ceiling_kwh)
        return SunsetProjection(
            projected_soc=100.0 * projected_stored / capacity_kwh,
            projected_stored_kwh=projected_stored,
            projected_charge_kwh=max(projected_stored - stored_kwh, 0.0),
            projected_captured_ac_kwh=0.0,
            projected_available_ac_kwh=0.0,
            hours_to_sunset=duration_h,
            average_load_kw=0.0 if duration_h <= 0 else expected_load_remaining_kwh / duration_h,
            modeled_solar_start_kw=0.0,
            model="none",
        )

    average_load_kw = expected_load_remaining_kwh / duration_h
    effective_load_kw = max(average_load_kw - preferred_import_kw, 0.0)
    hours_to_peak = None
    if peak_time is not None:
        hours_to_peak = (peak_time - now).total_seconds() / 3600.0

    step_h = step_minutes / 60.0
    elapsed_h = 0.0
    available_ac_kwh = 0.0
    modeled_start_kw = 0.0
    model_name = "none"

    while elapsed_h < duration_h - 1e-9:
        dt_h = min(step_h, duration_h - elapsed_h)
        midpoint_h = elapsed_h + dt_h / 2.0
        solar_kw, modeled_start_kw, model_name = _solar_power_kw(
            midpoint_h,
            duration_h=duration_h,
            current_solar_kw=max(float(current_solar_w), 0.0) / 1000.0,
            remaining_solar_kwh=remaining_solar_kwh,
            hours_to_peak=hours_to_peak,
        )
        chargeable_kw = max(solar_kw - effective_load_kw, 0.0)
        available_ac_kwh += chargeable_kw * dt_h
        elapsed_h += dt_h

    captured_ac_kwh = available_ac_kwh * harvest_capture_factor
    stored_charge_kwh = captured_ac_kwh * charge_efficiency
    headroom_kwh = max(charge_ceiling_kwh - stored_kwh, 0.0)
    stored_charge_kwh = min(stored_charge_kwh, headroom_kwh)
    projected_stored = stored_kwh + stored_charge_kwh

    return SunsetProjection(
        projected_soc=100.0 * projected_stored / capacity_kwh,
        projected_stored_kwh=projected_stored,
        projected_charge_kwh=stored_charge_kwh,
        projected_captured_ac_kwh=captured_ac_kwh,
        projected_available_ac_kwh=available_ac_kwh,
        hours_to_sunset=duration_h,
        average_load_kw=average_load_kw,
        modeled_solar_start_kw=modeled_start_kw,
        model=model_name,
    )
