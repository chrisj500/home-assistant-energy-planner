from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NextSunsetForecast:
    """Stable next-sunset semantics for dashboards and automations."""

    date: str | None
    soc_pct: float | None
    expected_charge_kwh: float | None
    expected_grid_import_kwh: float | None
    expected_export_kwh: float | None
    source: str


def _float(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if isinstance(value, bool):
        return None
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _stored_gain_kwh(
    *,
    start_soc_pct: float | None,
    end_soc_pct: float | None,
    capacity_kwh: float,
) -> float | None:
    if start_soc_pct is None or end_soc_pct is None:
        return None
    return max((float(end_soc_pct) - float(start_soc_pct)) * max(float(capacity_kwh), 0.0) / 100.0, 0.0)


def select_next_sunset_forecast(
    *,
    phase: str,
    data: dict[str, Any],
    capacity_kwh: float,
) -> NextSunsetForecast:
    """Select the next future sunset rather than mirroring current SOC overnight.

    ``phase`` is one of ``before_sunrise``, ``daylight`` or ``after_sunset``.
    During daylight the Forecast.Solar scaled live shadow is preferred when it is
    active. Before sunrise the current day's day-ahead plan is used. After sunset
    the next-day plan becomes the next future sunset immediately.
    """
    capacity = max(float(capacity_kwh), 0.001)

    if phase == "daylight":
        target_date = str(data.get("today_plan_date") or "") or None
        shadow_soc = _float(data, "forecast_solar_shadow_projected_sunset_soc")
        if (
            data.get("forecast_solar_enhancement_status") == "shadow_active"
            and shadow_soc is not None
        ):
            return NextSunsetForecast(
                date=target_date,
                soc_pct=shadow_soc,
                expected_charge_kwh=_float(data, "forecast_solar_shadow_charge_to_sunset"),
                expected_grid_import_kwh=_float(data, "forecast_solar_shadow_grid_import_today"),
                expected_export_kwh=_float(data, "forecast_solar_shadow_export_today"),
                source="forecast_solar_scaled_live",
            )
        return NextSunsetForecast(
            date=target_date,
            soc_pct=_float(data, "projected_sunset_soc") or _float(data, "today_projected_sunset_soc"),
            expected_charge_kwh=_float(data, "projected_charge_to_sunset"),
            expected_grid_import_kwh=_float(data, "today_predicted_grid_import"),
            expected_export_kwh=_float(data, "today_predicted_export"),
            source="baseline_live",
        )

    if phase == "before_sunrise":
        target_date = str(data.get("today_plan_date") or "") or None
        current_soc = _float(data, "weighted_soc")
        discharge = max(_float(data, "today_recommended_presolar_discharge") or 0.0, 0.0)
        planned_start_soc = (
            max(current_soc - discharge / capacity * 100.0, 0.0)
            if current_soc is not None
            else None
        )
        sunset_soc = _float(data, "today_projected_sunset_soc")
        return NextSunsetForecast(
            date=target_date,
            soc_pct=sunset_soc,
            expected_charge_kwh=_stored_gain_kwh(
                start_soc_pct=planned_start_soc,
                end_soc_pct=sunset_soc,
                capacity_kwh=capacity,
            ),
            expected_grid_import_kwh=_float(data, "today_predicted_grid_import"),
            expected_export_kwh=_float(data, "today_predicted_export"),
            source="configured_day_ahead_today",
        )

    if phase == "after_sunset":
        target_date = str(data.get("next_day_plan_date") or "") or None
        start_soc = _float(data, "planned_next_day_start_soc")
        sunset_soc = _float(data, "projected_sunset_soc_tomorrow")
        return NextSunsetForecast(
            date=target_date,
            soc_pct=sunset_soc,
            expected_charge_kwh=_stored_gain_kwh(
                start_soc_pct=start_soc,
                end_soc_pct=sunset_soc,
                capacity_kwh=capacity,
            ),
            expected_grid_import_kwh=_float(data, "predicted_grid_import_tomorrow"),
            expected_export_kwh=_float(data, "predicted_export_tomorrow"),
            source="configured_day_ahead_next_day",
        )

    return NextSunsetForecast(
        date=None,
        soc_pct=None,
        expected_charge_kwh=None,
        expected_grid_import_kwh=None,
        expected_export_kwh=None,
        source="unavailable",
    )
