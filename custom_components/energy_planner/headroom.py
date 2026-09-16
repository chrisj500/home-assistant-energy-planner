from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

try:
    from .forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh
except ImportError:  # pragma: no cover - direct unit-test import
    from forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh


_MIN_RAW_REMAINING_KWH = 0.05
_MIN_SCALE = 0.25
_MAX_SCALE = 4.0


@dataclass(frozen=True, slots=True)
class CurrentDayForecastCorrection:
    points: list[IntervalPoint]
    scale_factor: float
    raw_remaining_kwh: float
    corrected_remaining_kwh: float | None
    source: str


def correct_current_day_points(
    *,
    points: list[IntervalPoint],
    reference: datetime,
    sunset: datetime | None,
    corrected_remaining_kwh: float | None,
) -> CurrentDayForecastCorrection:
    """Scale only today's paid interval curve to the trusted remaining-energy total.

    The interval forecast remains valuable for shape and timing, while the configured
    same-day remaining-energy sensor can incorporate local Enphase evidence. Future
    dates are intentionally left untouched so current-day observations do not leak
    into tomorrow's weather forecast.
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
    if corrected_remaining_kwh is None or raw_remaining < _MIN_RAW_REMAINING_KWH:
        return CurrentDayForecastCorrection(
            points=list(points),
            scale_factor=1.0,
            raw_remaining_kwh=raw_remaining,
            corrected_remaining_kwh=corrected_remaining_kwh,
            source="raw_interval_curve",
        )

    corrected = max(float(corrected_remaining_kwh), 0.0)
    scale = corrected / raw_remaining
    scale = min(max(scale, _MIN_SCALE), _MAX_SCALE)
    local_tz = reference.tzinfo
    today = reference.date()
    scaled_points = [
        IntervalPoint(
            at=point.at,
            watts=(
                point.watts * scale
                if point.at.astimezone(local_tz).date() == today
                else point.watts
            ),
        )
        for point in points
    ]
    return CurrentDayForecastCorrection(
        points=scaled_points,
        scale_factor=scale,
        raw_remaining_kwh=raw_remaining,
        corrected_remaining_kwh=corrected,
        source="locally_corrected_current_day_interval_curve",
    )
