from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAPACITY_KWH,
    CONF_CHARGE_LIMIT,
    CONF_SOC_1,
    CONF_SOC_2,
    CONF_SOC_3,
    CONF_SOC_WEIGHTS,
    CONF_SOLAR_REMAINING,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_EFFICIENCY,
    DEFAULT_PREFERRED_IMPORT_W,
    DEFAULT_WEIGHTS,
    OPT_CHARGE_EFFICIENCY,
    OPT_PREFERRED_IMPORT_W,
)
from .coordinator import _controller_settings, _num, _solar_window
from .enhanced_coordinator import _parse_weights
from .forecast_solar_shadow import forecast_horizon_days, interval_points_from_payload
from .headroom import correct_current_day_points
from .multiday import serialize_dynamic_load_days
from .rolling_ev import DaylightWindow, simulate_rolling_days
from .v017_coordinator import EnergyPlannerV017Coordinator


class EnergyPlannerV018Coordinator(EnergyPlannerV017Coordinator):
    """v0.1.18 exposes every modeled day instead of only the first risk day."""

    def _rolling_ev_outputs(self, baseline: dict) -> dict:
        output = super()._rolling_ev_outputs(baseline)
        payload = self._estimate_payload
        if not isinstance(payload, dict):
            return output

        now = dt_util.now()
        points = interval_points_from_payload(payload, now, assume_utc=True)
        if len(points) < 2:
            return output

        sunrise, sunset = _solar_window(self.hass, now.date())
        correction = correct_current_day_points(
            points=points,
            reference=now,
            sunrise=sunrise,
            sunset=sunset,
            corrected_remaining_kwh=_num(self.hass, self.cfg.get(CONF_SOLAR_REMAINING)),
        )
        points = correction.points

        try:
            planning_load_w = float(output.get("rolling_planning_base_load_w"))
        except (TypeError, ValueError):
            return output

        soc_values = (
            _num(self.hass, self.cfg.get(CONF_SOC_1)),
            _num(self.hass, self.cfg.get(CONF_SOC_2)),
            _num(self.hass, self.cfg.get(CONF_SOC_3)),
        )
        if any(value is None for value in soc_values):
            return output
        bank_socs = tuple(float(value) for value in soc_values if value is not None)
        if len(bank_socs) != 3:
            return output

        charge_limit = _num(self.hass, self.cfg.get(CONF_CHARGE_LIMIT))
        if charge_limit is None:
            return output

        weights = _parse_weights(self.cfg.get(CONF_SOC_WEIGHTS, DEFAULT_WEIGHTS))
        capacity = float(self.cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        total_weight = sum(weights)
        bank_capacities = tuple(capacity * weight / total_weight for weight in weights)
        reserve = baseline.get("effective_reserve_floor")
        reserve_pct = float(reserve) if isinstance(reserve, (int, float)) else 10.0
        overnight_drop = baseline.get("calibration_overnight_median_kw")
        overnight_drop_kw = (
            float(overnight_drop) if isinstance(overnight_drop, (int, float)) else 0.0
        )
        charge_efficiency = float(
            self.cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)
        )
        controller = _controller_settings(
            self.hass,
            float(self.cfg.get(OPT_PREFERRED_IMPORT_W, DEFAULT_PREFERRED_IMPORT_W)),
        )

        horizon = min(max(forecast_horizon_days(points, now), 1), 7)
        daylight_windows: list[DaylightWindow] = []
        for offset in range(horizon):
            target_date = now.date() + timedelta(days=offset)
            target_sunrise, target_sunset = _solar_window(self.hass, target_date)
            if target_sunrise is None or target_sunset is None:
                continue
            daylight_windows.append(
                DaylightWindow(
                    day=target_date,
                    sunrise=target_sunrise,
                    sunset=target_sunset,
                )
            )

        plans = simulate_rolling_days(
            points=points,
            reference=now,
            daylight_windows=daylight_windows,
            initial_bank_socs_pct=bank_socs,
            bank_capacities_kwh=bank_capacities,
            charge_limit_pct=float(charge_limit),
            reserve_pct=reserve_pct,
            charge_efficiency=charge_efficiency,
            controller=controller,
            average_load_kw=planning_load_w / 1000.0,
            overnight_drop_kw=overnight_drop_kw,
            step_minutes=5,
        )
        rows = serialize_dynamic_load_days(
            plans,
            average_load_kw=planning_load_w / 1000.0,
            charge_efficiency=charge_efficiency,
            current_day=now.date(),
            current_day_source=correction.source,
            current_day_scale_factor=correction.scale_factor,
        )
        risk_rows = [row for row in rows if row.get("dynamic_load_needed")]
        risk_dates = [str(row["date"]) for row in risk_rows]
        total_dynamic = sum(float(row["dynamic_load_needed_kwh"]) for row in risk_rows)
        next_3d_dynamic = sum(
            float(row["dynamic_load_needed_kwh"])
            for row in rows[:3]
            if row.get("dynamic_load_needed")
        )

        output.update(
            {
                "rolling_day_plans": rows,
                "rolling_dynamic_load_risk_dates": risk_dates,
                "rolling_dynamic_load_days_count": len(risk_rows),
                "rolling_dynamic_load_total_kwh": total_dynamic,
                "rolling_dynamic_load_next_3d_kwh": next_3d_dynamic,
                "rolling_dynamic_load_forecast_status": (
                    "dynamic_load_needed" if risk_rows else "clear"
                ),
                "rolling_dynamic_load_forecast_model": "sequential_7d_headroom_v1",
            }
        )
        return output
