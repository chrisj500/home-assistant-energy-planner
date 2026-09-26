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

        def state_snapshot(entity_id):
            state = self.hass.states.get(entity_id) if entity_id else None
            return {
                "entity_id": entity_id,
                "state": None if state is None else state.state,
                "available": (
                    state is not None
                    and state.state not in {"unknown", "unavailable", "none", ""}
                ),
            }

        def unavailable(reason, **details):
            output.update(
                battery_outlook_status="unavailable",
                battery_outlook_reason=reason,
                battery_outlook_inputs=details,
            )
            return output

        payload = self._estimate_payload
        if not isinstance(payload, dict):
            return unavailable(
                "Interval solar forecast unavailable",
                interval_status=output.get("rolling_ev_status"),
            )

        now = dt_util.now()
        points = interval_points_from_payload(payload, now, assume_utc=True)
        if len(points) < 2:
            return unavailable(
                "Interval solar forecast has fewer than two future points",
                interval_source=getattr(self, "_estimate_origin", "unknown"),
                interval_error=getattr(self, "_estimate_error", None),
            )

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
            return unavailable(
                "Planning base load unavailable",
                rolling_status=output.get("rolling_ev_status"),
                planning_source=output.get("rolling_planning_base_load_source"),
            )

        topology_socs = baseline.get("battery_bank_socs_pct")
        topology_capacities = baseline.get("battery_bank_capacities_kwh")
        topology_capacity = baseline.get("battery_capacity_kwh")
        if (
            not isinstance(topology_socs, (list, tuple))
            or len(topology_socs) != 3
            or not isinstance(topology_capacities, (list, tuple))
            or len(topology_capacities) != 3
            or not isinstance(topology_capacity, (int, float))
        ):
            return unavailable(
                "Battery topology is unavailable",
                topology_source=baseline.get("battery_topology_source"),
                topology_reason=baseline.get("battery_topology_reason"),
            )
        bank_socs = tuple(float(value) for value in topology_socs)
        bank_capacities = tuple(float(value) for value in topology_capacities)
        capacity = float(topology_capacity)

        charge_limit_entity = self.cfg.get(CONF_CHARGE_LIMIT)
        charge_limit = _num(self.hass, charge_limit_entity)
        if charge_limit is None:
            return unavailable(
                "Battery charge limit is unavailable or non-numeric",
                charge_limit=state_snapshot(charge_limit_entity),
            )

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

        if not daylight_windows:
            return unavailable(
                "No solar daylight windows are available for the forecast horizon"
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
        if not rows:
            return unavailable(
                "Battery simulation produced no modeled days",
                interval_source=getattr(self, "_estimate_origin", "unknown"),
                forecast_points=len(points),
                daylight_windows=len(daylight_windows),
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
                "battery_outlook_status": "ready",
                "battery_outlook_reason": "Battery simulation inputs are available",
                "battery_outlook_inputs": {
                    "interval_source": getattr(self, "_estimate_origin", "unknown"),
                    "forecast_points": len(points),
                    "planning_load_w": planning_load_w,
                    "charge_limit_pct": float(charge_limit),
                    "battery_soc_pct": list(bank_socs),
                    "battery_capacity_kwh": capacity,
                    "battery_bank_capacities_kwh": list(bank_capacities),
                    "battery_topology_source": baseline.get("battery_topology_source"),
                    "battery_pack_counts": baseline.get("battery_pack_counts"),
                    "daylight_windows": len(daylight_windows),
                },
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
