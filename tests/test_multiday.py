from datetime import date, datetime, timezone

from custom_components.energy_planner.multiday import (
    dynamic_load_required_kwh,
    serialize_dynamic_load_days,
)
from custom_components.energy_planner.rolling_ev import RollingDayPlan


def _plan(*, day: date, shortfall: float, capacity_export: float) -> RollingDayPlan:
    start = datetime(day.year, day.month, day.day, 8, tzinfo=timezone.utc)
    end = datetime(day.year, day.month, day.day, 18, tzinfo=timezone.utc)
    return RollingDayPlan(
        day=day,
        start=start,
        end=end,
        start_soc_pct=20.0,
        end_soc_pct=98.0 if shortfall or capacity_export else 75.0,
        solar_kwh=60.0,
        stored_charge_kwh=30.0,
        grid_import_kwh=1.0,
        export_kwh=capacity_export,
        capacity_export_kwh=capacity_export,
        power_export_kwh=0.0,
        required_headroom_kwh=35.0,
        available_headroom_kwh=35.0 - shortfall,
        headroom_margin_kwh=-shortfall,
        headroom_shortfall_kwh=shortfall,
        ending_bank_socs_pct=(98.0, 98.0, 98.0),
    )


def test_dynamic_load_uses_larger_ac_requirement() -> None:
    plan = _plan(day=date(2026, 9, 16), shortfall=0.9, capacity_export=0.6)
    assert dynamic_load_required_kwh(plan, charge_efficiency=0.9) == 1.0


def test_multiday_rows_keep_all_days_and_mark_each_risk() -> None:
    plans = [
        _plan(day=date(2026, 9, 16), shortfall=0.5, capacity_export=1.6),
        _plan(day=date(2026, 9, 17), shortfall=0.0, capacity_export=0.0),
        _plan(day=date(2026, 9, 18), shortfall=2.0, capacity_export=2.1),
    ]
    rows = serialize_dynamic_load_days(
        plans,
        average_load_kw=3.0,
        charge_efficiency=0.9,
        current_day=date(2026, 9, 16),
        current_day_source="locally_corrected_current_day_interval_curve",
        current_day_scale_factor=1.53,
    )

    assert [row["date"] for row in rows] == ["2026-09-16", "2026-09-17", "2026-09-18"]
    assert rows[0]["dynamic_load_needed"] is True
    assert rows[1]["dynamic_load_needed"] is False
    assert rows[2]["dynamic_load_needed"] is True
    assert rows[0]["dynamic_load_needed_kwh"] == 1.6
    assert rows[2]["dynamic_load_needed_kwh"] == 2.22
    assert rows[0]["forecast_scale_factor"] == 1.53
    assert rows[1]["forecast_scale_factor"] == 1.0
