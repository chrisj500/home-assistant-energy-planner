from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any


@dataclass(frozen=True, slots=True)
class IntervalPoint:
    at: datetime
    watts: float


def coerce_datetime(
    value: Any,
    reference: datetime,
    *,
    assume_utc: bool = False,
) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC if assume_utc else reference.tzinfo)
    return parsed


def interval_points_from_payload(
    payload: Any,
    reference: datetime,
    *,
    assume_utc: bool = True,
) -> list[IntervalPoint]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    watts = result.get("watts")
    if not isinstance(watts, dict):
        return []
    points: list[IntervalPoint] = []
    for raw_time, raw_watts in watts.items():
        at = coerce_datetime(raw_time, reference, assume_utc=assume_utc)
        if at is None:
            continue
        try:
            value = max(float(raw_watts), 0.0)
        except (TypeError, ValueError):
            continue
        points.append(IntervalPoint(at=at, watts=value))
    points.sort(key=lambda point: point.at)
    return points


def interval_resolution_minutes(points: list[IntervalPoint]) -> float | None:
    deltas = [
        (right.at - left.at).total_seconds() / 60.0
        for left, right in zip(points, points[1:])
        if right.at > left.at
    ]
    if not deltas:
        return None
    return float(median(deltas))


def forecast_horizon_days(points: list[IntervalPoint], reference: datetime) -> int:
    local_tz = reference.tzinfo
    future_dates = {
        point.at.astimezone(local_tz).date()
        for point in points
        if point.at >= reference
    }
    if not future_dates:
        return 0
    return (max(future_dates) - reference.date()).days + 1


def power_at(points: list[IntervalPoint], at: datetime) -> float:
    if not points:
        return 0.0
    times = [point.at for point in points]
    index = bisect_right(times, at)
    if index <= 0:
        return points[0].watts
    if index >= len(points):
        return 0.0
    left = points[index - 1]
    right = points[index]
    span = (right.at - left.at).total_seconds()
    if span <= 0:
        return left.watts
    fraction = (at - left.at).total_seconds() / span
    return left.watts + (right.watts - left.watts) * fraction


def integrate_interval_energy_kwh(
    points: list[IntervalPoint],
    start: datetime,
    end: datetime,
    *,
    step_minutes: int = 1,
) -> float:
    if end <= start or not points:
        return 0.0
    step = timedelta(minutes=max(int(step_minutes), 1))
    cursor = start
    total = 0.0
    while cursor < end:
        next_cursor = min(cursor + step, end)
        midpoint = cursor + (next_cursor - cursor) / 2
        hours = (next_cursor - cursor).total_seconds() / 3600.0
        total += power_at(points, midpoint) / 1000.0 * hours
        cursor = next_cursor
    return total


def safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def weather_rows(payload: Any, reference: datetime) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        at = coerce_datetime(item.get("datetime"), reference, assume_utc=False)
        if at is None:
            continue
        row = dict(item)
        row["_at"] = at
        rows.append(row)
    rows.sort(key=lambda item: item["_at"])
    return rows


def energy_remaining_from_result(
    payload: Any,
    reference: datetime,
    sunset: datetime | None,
) -> float | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    periods = result.get("watt_hours_period")
    if not isinstance(periods, dict):
        return None
    total_wh = 0.0
    found = False
    for raw_time, raw_wh in periods.items():
        at = coerce_datetime(raw_time, reference, assume_utc=True)
        if at is None or at < reference:
            continue
        if sunset is not None and at > sunset + timedelta(minutes=30):
            continue
        try:
            total_wh += max(float(raw_wh), 0.0)
            found = True
        except (TypeError, ValueError):
            continue
    return total_wh / 1000.0 if found else None


def best_time_window(payload: Any, reference: datetime) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, list):
        return None
    candidates: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        start = coerce_datetime(item.get("start"), reference)
        end = coerce_datetime(item.get("end"), reference)
        watts = safe_float(item.get("watts"))
        watthours = safe_float(item.get("watthours"))
        if start is None or end is None or watts is None or watthours is None:
            continue
        candidates.append(
            {
                "start": start,
                "end": end,
                "watts": max(watts, 0.0),
                "watthours": max(watthours, 0.0),
            }
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["watthours"])
