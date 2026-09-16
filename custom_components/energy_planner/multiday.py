from __future__ import annotations

from datetime import date

try:
    from .rolling_ev import RollingDayPlan
except ImportError:  # pragma: no cover - direct unit-test import
    from rolling_ev import RollingDayPlan


DEFAULT_RISK_THRESHOLD_KWH = 0.25


def dynamic_load_required_kwh(
    plan: RollingDayPlan,
    *,
    charge_efficiency: float,
) -> float:
    """Return useful AC load needed to avoid capacity-driven solar export.

    Capacity-limited export is already measured on the AC side. Headroom shortfall
    is stored-energy shortfall, so it is converted back to the AC energy that would
    have needed to be diverted into a useful dynamic load instead of the battery.
    """
    efficiency = min(max(float(charge_efficiency), 0.01), 1.0)
    return max(
        max(float(plan.capacity_export_kwh), 0.0),
        max(float(plan.headroom_shortfall_kwh), 0.0) / efficiency,
    )


def serialize_dynamic_load_days(
    plans: list[RollingDayPlan],
    *,
    average_load_kw: float,
    charge_efficiency: float,
    current_day: date,
    current_day_source: str,
    current_day_scale_factor: float,
    risk_threshold_kwh: float = DEFAULT_RISK_THRESHOLD_KWH,
) -> list[dict[str, object]]:
    """Convert sequential rolling plans into dashboard-safe per-day decisions."""
    threshold = max(float(risk_threshold_kwh), 0.0)
    load_kw = max(float(average_load_kw), 0.0)
    rows: list[dict[str, object]] = []

    for plan in plans:
        duration_h = max((plan.end - plan.start).total_seconds() / 3600.0, 0.0)
        solar_after_house_load = max(float(plan.solar_kwh) - load_kw * duration_h, 0.0)
        dynamic_load = dynamic_load_required_kwh(
            plan,
            charge_efficiency=charge_efficiency,
        )
        risk = (
            float(plan.headroom_shortfall_kwh) >= threshold
            or float(plan.capacity_export_kwh) >= threshold
        )
        is_today = plan.day == current_day
        rows.append(
            {
                "date": plan.day.isoformat(),
                "start_soc_pct": round(float(plan.start_soc_pct), 2),
                "sunset_soc_pct": round(float(plan.end_soc_pct), 2),
                "solar_kwh": round(float(plan.solar_kwh), 2),
                "solar_after_house_load_kwh": round(solar_after_house_load, 2),
                "battery_gain_kwh": round(float(plan.stored_charge_kwh), 2),
                "grid_import_kwh": round(float(plan.grid_import_kwh), 2),
                "export_kwh": round(float(plan.export_kwh), 2),
                "capacity_export_kwh": round(float(plan.capacity_export_kwh), 2),
                "power_export_kwh": round(float(plan.power_export_kwh), 2),
                "available_headroom_kwh": round(float(plan.available_headroom_kwh), 2),
                "required_headroom_kwh": round(float(plan.required_headroom_kwh), 2),
                "headroom_margin_kwh": round(float(plan.headroom_margin_kwh), 2),
                "headroom_shortfall_kwh": round(float(plan.headroom_shortfall_kwh), 2),
                "dynamic_load_needed_kwh": round(dynamic_load, 2),
                "dynamic_load_needed": risk,
                "status": "use_dynamic_load" if risk else "clear",
                "forecast_source": (
                    current_day_source if is_today else "forecast_solar_paid_interval_curve"
                ),
                "forecast_scale_factor": (
                    round(float(current_day_scale_factor), 3) if is_today else 1.0
                ),
            }
        )

    return rows
