from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    from .solar_policy import LIVE_BLEND_MINUTES
    from .forecast_solar_shadow import (
        IntervalPoint,
        integrate_interval_energy_kwh,
        power_at,
    )
except ImportError:  # pragma: no cover - direct unit-test import
    from solar_policy import LIVE_BLEND_MINUTES
    from forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh, power_at


_DEFAULT_LIVE_BLEND_MINUTES = LIVE_BLEND_MINUTES
_MIN_LIVE_BLEND_MINUTES = 5.0


@dataclass(frozen=True, slots=True)
class CurrentDayForecastCorrection:
    points: list[IntervalPoint]
    scale_factor: float
    raw_remaining_kwh: float
    corrected_remaining_kwh: float | None
    source: str
    live_anchor_w: float | None = None
    live_blend_minutes: float | None = None
    compensated_remaining_kwh: float | None = None


def _dedupe_sorted(points: list[IntervalPoint]) -> list[IntervalPoint]:
    by_time: dict[datetime, float] = {}
    for point in points:
        by_time[point.at] = max(float(point.watts), 0.0)
    return [IntervalPoint(at=at, watts=by_time[at]) for at in sorted(by_time)]


def _live_anchor_curve(
    *,
    points: list[IntervalPoint],
    reference: datetime,
    sunset: datetime,
    actual_solar_w: float,
    blend_minutes: float,
) -> tuple[list[IntervalPoint], float, float]:
    """Blend live power into the next hour without altering later forecast energy."""
    if sunset <= reference or not points:
        return list(points), 0.0, 0.0

    duration_s = max((sunset - reference).total_seconds(), 1.0)
    requested_blend_s = max(float(blend_minutes), _MIN_LIVE_BLEND_MINUTES) * 60.0
    blend_s = min(requested_blend_s, duration_s)
    blend_end = reference + timedelta(seconds=blend_s)

    base_now_w = max(power_at(points, reference), 0.0)
    actual_w = max(float(actual_solar_w), 0.0)
    delta_w = actual_w - base_now_w

    local_tz = reference.tzinfo
    today = reference.date()

    base_points = list(points)
    base_points.extend(
        [
            IntervalPoint(reference, base_now_w),
            IntervalPoint(blend_end, max(power_at(points, blend_end), 0.0)),
            IntervalPoint(sunset, max(power_at(points, sunset), 0.0)),
        ]
    )
    base_points = _dedupe_sorted(base_points)

    def transformed() -> list[IntervalPoint]:
        adjusted: list[IntervalPoint] = []
        for point in base_points:
            local_day = point.at.astimezone(local_tz).date()
            if (
                local_day != today
                or point.at < reference
                or point.at > sunset
            ):
                adjusted.append(point)
                continue

            elapsed_s = min(
                max((point.at - reference).total_seconds(), 0.0),
                duration_s,
            )
            live_weight = max(1.0 - elapsed_s / max(blend_s, 1.0), 0.0)
            watts = (
                point.watts
                + delta_w * live_weight
            )
            adjusted.append(IntervalPoint(point.at, max(watts, 0.0)))
        return _dedupe_sorted(adjusted)

    anchored = transformed()
    return anchored, blend_s / 60.0, integrate_interval_energy_kwh(anchored, reference, sunset)


def correct_current_day_points(
    *,
    points: list[IntervalPoint],
    reference: datetime,
    sunrise: datetime | None = None,
    sunset: datetime | None,
    corrected_remaining_kwh: float | None,
    actual_solar_w: float | None = None,
    live_blend_minutes: float = _DEFAULT_LIVE_BLEND_MINUTES,
) -> CurrentDayForecastCorrection:
    """Use provider energy and a short-lived live-power adjustment.

    The legacy corrected_remaining_kwh argument is accepted for compatibility but
    never scales the provider curve. Solar policy belongs to this integration.
    """
    if sunset is None or sunset <= reference or not points:
        return CurrentDayForecastCorrection(
            points=list(points),
            scale_factor=1.0,
            raw_remaining_kwh=0.0,
            corrected_remaining_kwh=corrected_remaining_kwh,
            source="raw_interval_curve",
        )

    raw_remaining = integrate_interval_energy_kwh(points, reference, sunset)
    corrected = raw_remaining
    scale = 1.0
    scaled_points = list(points)
    source = "raw_interval_curve"
    live_anchor_w = None
    applied_blend_minutes = None
    compensated_remaining = integrate_interval_energy_kwh(
        scaled_points, reference, sunset
    )

    daylight = (
        actual_solar_w is not None
        and (sunrise is None or reference >= sunrise)
        and reference < sunset
    )
    if daylight:
        scaled_points, applied_blend_minutes, compensated_remaining = _live_anchor_curve(
            points=scaled_points,
            reference=reference,
            sunset=sunset,
            actual_solar_w=float(actual_solar_w),
            blend_minutes=live_blend_minutes,
        )
        live_anchor_w = max(float(actual_solar_w), 0.0)
        source = "live_anchored_current_day_interval_curve"

    return CurrentDayForecastCorrection(
        points=scaled_points,
        scale_factor=scale,
        raw_remaining_kwh=raw_remaining,
        corrected_remaining_kwh=corrected,
        source=source,
        live_anchor_w=live_anchor_w,
        live_blend_minutes=applied_blend_minutes,
        compensated_remaining_kwh=compensated_remaining,
    )
