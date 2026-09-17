from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    from .forecast_solar_shadow import (
        IntervalPoint,
        integrate_interval_energy_kwh,
        power_at,
    )
except ImportError:  # pragma: no cover - direct unit-test import
    from forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh, power_at


_MIN_RAW_REMAINING_KWH = 0.05
_MIN_CORRECTED_REMAINING_KWH = 0.05
_MIN_SCALE = 0.25
_MAX_SCALE = 4.0
_DEFAULT_LIVE_BLEND_MINUTES = 60.0
_MIN_LIVE_BLEND_MINUTES = 5.0
_MAX_COMPENSATION = 64.0


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
    """Anchor the current-day interval curve to live production without changing energy.

    The corrected Forecast.Solar curve still owns the remaining-energy total. Live
    Enphase production owns the instantaneous value at ``reference``. A decaying
    live offset is blended away over roughly an hour, while a smooth compensation
    term redistributes the same energy later in the day. This shifts *timing* toward
    observed conditions without inventing or deleting forecast energy.
    """
    if sunset <= reference or not points:
        return list(points), 0.0, 0.0

    target_kwh = integrate_interval_energy_kwh(points, reference, sunset)
    if target_kwh <= 0.0:
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

    def transformed(compensation: float) -> list[IntervalPoint]:
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
            compensation_weight = elapsed_s / duration_s
            watts = (
                point.watts
                + delta_w * live_weight
                - compensation * point.watts * compensation_weight
            )
            adjusted.append(IntervalPoint(point.at, max(watts, 0.0)))
        return _dedupe_sorted(adjusted)

    anchored = transformed(0.0)
    anchored_kwh = integrate_interval_energy_kwh(anchored, reference, sunset)
    if abs(anchored_kwh - target_kwh) <= 0.001:
        return anchored, blend_s / 60.0, anchored_kwh

    # Energy is monotonic with the compensation coefficient. Bracket the target,
    # then bisect. Clamping at zero is handled by evaluating the actual curve.
    if anchored_kwh > target_kwh:
        low = 0.0
        high = 1.0
        high_energy = integrate_interval_energy_kwh(
            transformed(high), reference, sunset
        )
        while high_energy > target_kwh and high < _MAX_COMPENSATION:
            high *= 2.0
            high_energy = integrate_interval_energy_kwh(
                transformed(high), reference, sunset
            )
    else:
        high = 0.0
        low = -1.0
        low_energy = integrate_interval_energy_kwh(
            transformed(low), reference, sunset
        )
        while low_energy < target_kwh and abs(low) < _MAX_COMPENSATION:
            low *= 2.0
            low_energy = integrate_interval_energy_kwh(
                transformed(low), reference, sunset
            )

    best = anchored
    for _ in range(36):
        middle = (low + high) / 2.0
        candidate = transformed(middle)
        candidate_kwh = integrate_interval_energy_kwh(candidate, reference, sunset)
        best = candidate
        if candidate_kwh > target_kwh:
            low = middle
        else:
            high = middle

    final_kwh = integrate_interval_energy_kwh(best, reference, sunset)
    return best, blend_s / 60.0, final_kwh


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
    """Correct today's interval curve using local energy and live solar evidence.

    The paid interval forecast remains valuable for shape and timing. First, today's
    curve is scaled to the configured locally corrected remaining-energy total. When
    live solar production is available during daylight, the curve is then anchored
    to that measured power at ``reference`` and blended back toward Forecast.Solar
    over roughly an hour while preserving the corrected remaining-energy total.

    Future dates are intentionally untouched so current-day observations do not leak
    into tomorrow's weather forecast.

    Immediately after midnight, some same-day remaining-energy sensors briefly reset
    to zero before their new-day forecast is populated. Before sunrise, a near-zero
    corrected total is therefore ignored when the paid interval curve still contains
    material solar energy for the day. Once daylight begins, the local correction is
    trusted normally, including legitimate near-zero remaining-energy values late in
    the solar day.
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
    if (
        sunrise is not None
        and reference < sunrise
        and corrected <= _MIN_CORRECTED_REMAINING_KWH
        and raw_remaining >= _MIN_RAW_REMAINING_KWH
    ):
        return CurrentDayForecastCorrection(
            points=list(points),
            scale_factor=1.0,
            raw_remaining_kwh=raw_remaining,
            corrected_remaining_kwh=corrected,
            source="raw_interval_curve_pre_sunrise_rollover",
        )

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

    source = "locally_corrected_current_day_interval_curve"
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
