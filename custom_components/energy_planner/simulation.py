from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class ControllerSettings:
    """Forecast mirror of the EcoFlow surplus-controller policy."""

    minimum_rate_w: float = 500.0
    maximum_rate_w: float = 3900.0
    rate_step_w: float = 100.0
    maximum_rate_increase_w: float = 800.0
    slow_import_decrease_w: float = 200.0
    moderate_import_decrease_w: float = 500.0
    preferred_import_w: float = 250.0
    import_hold_high_w: float = 350.0
    moderate_import_threshold_w: float = 1000.0
    severe_import_threshold_w: float = 2000.0
    start_export_w: float = 150.0
    export_gain: float = 1.0
    import_gain: float = 0.8
    minimum_solar_w: float = 150.0
    stop_all_w: float = 250.0
    start_2_w: float = 1800.0
    stop_2_w: float = 1100.0
    start_3_w: float = 3300.0
    stop_3_w: float = 2400.0
    enabled: bool = True
    source: str = "defaults"


@dataclass(frozen=True, slots=True)
class SimulationResult:
    ending_bank_socs_pct: tuple[float, float, float]
    aggregate_soc_pct: float
    stored_charge_kwh: float
    battery_ac_charge_kwh: float
    solar_to_battery_ac_kwh: float
    grid_to_battery_ac_kwh: float
    predicted_export_kwh: float
    predicted_grid_import_kwh: float
    capacity_limited_export_kwh: float
    power_limited_export_kwh: float
    control_limited_export_kwh: float
    peak_export_w: float
    export_minutes: float
    average_load_kw: float
    model: str


@dataclass(frozen=True, slots=True)
class ControllerState:
    mask: int = 0
    rate_w: int = 500


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _mask_count(mask: int) -> int:
    return sum(1 for bit in (1, 2, 4) if mask & bit)


def _round_to_step(value: float, step: float) -> int:
    step = step if step > 0 else 100.0
    return int(round(value / step) * step)


def _select_mask(
    eligible: tuple[bool, bool, bool],
    socs: tuple[float, float, float],
    desired_count: int,
    commanded_mask: int,
) -> int:
    if desired_count <= 0:
        return 0

    commanded_count = _mask_count(commanded_mask)
    commanded_valid = all(
        not (commanded_mask & bit) or eligible[index]
        for index, bit in enumerate((1, 2, 4))
    )
    if desired_count == commanded_count and commanded_valid:
        return commanded_mask

    candidates = [
        (socs[index], index, bit)
        for index, bit in enumerate((1, 2, 4))
        if eligible[index]
    ]
    candidates.sort(key=lambda item: (item[0], item[1]))
    return sum(bit for _, _, bit in candidates[:desired_count])


def _controller_decision(
    *,
    site_grid_w: float,
    solar_w: float,
    socs: tuple[float, float, float],
    eligible: tuple[bool, bool, bool],
    state: ControllerState,
    settings: ControllerSettings,
) -> ControllerState:
    """Mirror the deployed controller's side-effect-free decision math."""
    if not settings.enabled:
        return ControllerState(mask=0, rate_w=int(settings.minimum_rate_w))

    commanded_count = _mask_count(state.mask)
    eligible_count = sum(1 for value in eligible if value)
    control_ok = solar_w >= settings.minimum_solar_w and eligible_count > 0
    safe_to_start = control_ok and site_grid_w <= -settings.start_export_w
    expected_command_w = commanded_count * state.rate_w
    in_import_hold = 0 <= site_grid_w <= settings.import_hold_high_w

    if not control_ok:
        target_command_w = 0.0
    elif in_import_hold:
        target_command_w = float(expected_command_w)
    else:
        gain = settings.export_gain if site_grid_w < 0 else settings.import_gain
        raw = expected_command_w + gain * (
            settings.preferred_import_w - site_grid_w
        )
        target_command_w = min(
            max(raw, 0.0), settings.maximum_rate_w * eligible_count
        )

    near_min = state.rate_w <= (
        settings.minimum_rate_w + settings.slow_import_decrease_w
    )

    if not control_ok:
        desired_count_pre = 0
    elif site_grid_w > settings.severe_import_threshold_w:
        if target_command_w < settings.stop_all_w:
            desired_count_pre = 0
        elif target_command_w < settings.start_2_w:
            desired_count_pre = 1
        elif target_command_w < settings.start_3_w:
            desired_count_pre = 2
        else:
            desired_count_pre = 3
    elif commanded_count == 0:
        desired_count_pre = 1 if safe_to_start else 0
    elif in_import_hold:
        desired_count_pre = commanded_count
    elif commanded_count == 1:
        if near_min and (
            target_command_w < settings.stop_all_w
            or site_grid_w > settings.import_hold_high_w
        ):
            desired_count_pre = 0
        elif target_command_w >= settings.start_2_w:
            desired_count_pre = 2
        else:
            desired_count_pre = 1
    elif commanded_count == 2:
        if target_command_w >= settings.start_3_w:
            desired_count_pre = 3
        elif target_command_w < settings.stop_2_w and near_min:
            desired_count_pre = 1
        else:
            desired_count_pre = 2
    else:
        if target_command_w < settings.stop_3_w and near_min:
            desired_count_pre = 2
        else:
            desired_count_pre = 3

    desired_count = min(desired_count_pre, eligible_count)
    desired_mask = _select_mask(eligible, socs, desired_count, state.mask)

    if desired_count <= 0:
        ideal_rate_w = int(settings.minimum_rate_w)
    else:
        raw_rate = target_command_w / desired_count
        limited = min(
            max(raw_rate, settings.minimum_rate_w), settings.maximum_rate_w
        )
        ideal_rate_w = _round_to_step(limited, settings.rate_step_w)

    if site_grid_w > settings.severe_import_threshold_w:
        decrease_cap_w = 99999.0
    elif site_grid_w > settings.moderate_import_threshold_w:
        decrease_cap_w = settings.moderate_import_decrease_w
    else:
        decrease_cap_w = settings.slow_import_decrease_w

    if desired_count <= 0:
        command_rate_w = int(settings.minimum_rate_w)
    elif desired_count != commanded_count:
        command_rate_w = ideal_rate_w
    elif ideal_rate_w > state.rate_w:
        command_rate_w = int(
            min(
                ideal_rate_w,
                state.rate_w + settings.maximum_rate_increase_w,
            )
        )
    elif ideal_rate_w < state.rate_w:
        if site_grid_w > settings.severe_import_threshold_w:
            command_rate_w = ideal_rate_w
        else:
            command_rate_w = int(
                max(ideal_rate_w, state.rate_w - decrease_cap_w)
            )
    else:
        command_rate_w = int(state.rate_w)

    return ControllerState(mask=desired_mask, rate_w=command_rate_w)


def full_day_solar_curve(
    *,
    solar_forecast_kwh: float,
    daylight_hours: float,
    peak_elapsed_h: float,
) -> Callable[[float], float]:
    """Return the energy-conserving triangular full-day solar model in kW."""
    daylight_hours = max(float(daylight_hours), 0.0)
    solar_forecast_kwh = max(float(solar_forecast_kwh), 0.0)
    if daylight_hours <= 0 or solar_forecast_kwh <= 0:
        return lambda _elapsed_h: 0.0

    peak_elapsed_h = _clamp(float(peak_elapsed_h), 0.05, daylight_hours - 0.05)
    peak_kw = 2.0 * solar_forecast_kwh / daylight_hours

    def _curve(elapsed_h: float) -> float:
        elapsed_h = _clamp(float(elapsed_h), 0.0, daylight_hours)
        if elapsed_h <= peak_elapsed_h:
            return peak_kw * elapsed_h / peak_elapsed_h
        tail_h = daylight_hours - peak_elapsed_h
        return peak_kw * (daylight_hours - elapsed_h) / tail_h

    return _curve


def simulate_energy_flow(
    *,
    duration_h: float,
    solar_power_kw: Callable[[float], float],
    average_load_kw: float,
    bank_socs_pct: tuple[float, float, float],
    bank_capacities_kwh: tuple[float, float, float],
    charge_limit_pct: float,
    charge_efficiency: float,
    controller: ControllerSettings,
    step_minutes: int = 1,
    ignore_battery_capacity: bool = False,
) -> SimulationResult:
    """Simulate physical AC flows and the fast controller in quasi-steady state.

    Export is counted only when modeled solar cannot be absorbed after house
    load and the controller's actual charging policy. Charge efficiency affects
    stored battery energy; it is never reclassified as grid export.
    """
    duration_h = max(float(duration_h), 0.0)
    average_load_kw = max(float(average_load_kw), 0.0)
    charge_limit_pct = _clamp(float(charge_limit_pct), 0.0, 100.0)
    charge_efficiency = _clamp(float(charge_efficiency), 0.0, 1.0)
    step_minutes = max(int(step_minutes), 1)

    capacities = tuple(max(float(value), 0.001) for value in bank_capacities_kwh)
    stored = [
        capacity * _clamp(float(soc), 0.0, 100.0) / 100.0
        for capacity, soc in zip(capacities, bank_socs_pct)
    ]
    charge_ceiling = [capacity * charge_limit_pct / 100.0 for capacity in capacities]

    controller_state = ControllerState(rate_w=int(controller.minimum_rate_w))
    stored_charge_kwh = 0.0
    battery_ac_charge_kwh = 0.0
    solar_to_battery_ac_kwh = 0.0
    grid_to_battery_ac_kwh = 0.0
    predicted_export_kwh = 0.0
    predicted_grid_import_kwh = 0.0
    capacity_limited_export_kwh = 0.0
    power_limited_export_kwh = 0.0
    control_limited_export_kwh = 0.0
    peak_export_w = 0.0
    export_minutes = 0.0

    step_h = step_minutes / 60.0
    elapsed_h = 0.0
    while elapsed_h < duration_h - 1e-9:
        dt_h = min(step_h, duration_h - elapsed_h)
        midpoint_h = elapsed_h + dt_h / 2.0
        solar_w = max(float(solar_power_kw(midpoint_h)), 0.0) * 1000.0
        load_w = average_load_kw * 1000.0

        if ignore_battery_capacity:
            eligible = (True, True, True)
        else:
            eligible = tuple(
                stored[index] < charge_ceiling[index] - 1e-6
                for index in range(3)
            )

        current_socs = tuple(
            100.0 * stored[index] / capacities[index]
            for index in range(3)
        )

        # The live controller retriggers after roughly two seconds of a fresh
        # grid reading. Forecast steps are much coarser, so converge the control
        # reaction inside each step rather than pretending it responds only once.
        for _ in range(12):
            commanded_charge_w = _mask_count(controller_state.mask) * controller_state.rate_w
            site_grid_w = load_w + commanded_charge_w - solar_w
            next_state = _controller_decision(
                site_grid_w=site_grid_w,
                solar_w=solar_w,
                socs=current_socs,
                eligible=eligible,
                state=controller_state,
                settings=controller,
            )
            if next_state == controller_state:
                break
            controller_state = next_state

        accepted_ac_by_bank = [0.0, 0.0, 0.0]
        for index, bit in enumerate((1, 2, 4)):
            if not (controller_state.mask & bit):
                continue
            commanded_ac_kwh = controller_state.rate_w / 1000.0 * dt_h
            if ignore_battery_capacity:
                accepted_ac_kwh = commanded_ac_kwh
            elif charge_efficiency <= 0:
                accepted_ac_kwh = 0.0
            else:
                headroom_stored_kwh = max(charge_ceiling[index] - stored[index], 0.0)
                accepted_ac_kwh = min(
                    commanded_ac_kwh,
                    headroom_stored_kwh / charge_efficiency,
                )
            accepted_ac_by_bank[index] = accepted_ac_kwh

        accepted_ac_kwh = sum(accepted_ac_by_bank)
        actual_charge_w = accepted_ac_kwh / dt_h * 1000.0 if dt_h > 0 else 0.0
        natural_surplus_w = max(solar_w - load_w, 0.0)
        site_grid_w = load_w + actual_charge_w - solar_w
        export_w = max(-site_grid_w, 0.0)
        import_w = max(site_grid_w, 0.0)

        solar_charge_w = min(actual_charge_w, natural_surplus_w)
        grid_charge_w = max(actual_charge_w - natural_surplus_w, 0.0)
        solar_to_battery_ac_kwh += solar_charge_w / 1000.0 * dt_h
        grid_to_battery_ac_kwh += grid_charge_w / 1000.0 * dt_h
        battery_ac_charge_kwh += accepted_ac_kwh
        predicted_export_kwh += export_w / 1000.0 * dt_h
        predicted_grid_import_kwh += import_w / 1000.0 * dt_h
        peak_export_w = max(peak_export_w, export_w)
        if export_w > 1.0:
            export_minutes += dt_h * 60.0

        if export_w > 0:
            remaining_export_w = export_w
            eligible_count = sum(1 for value in eligible if value)
            if not ignore_battery_capacity and eligible_count == 0:
                capacity_piece_w = remaining_export_w
                power_piece_w = 0.0
            else:
                controller_power_ceiling_w = controller.maximum_rate_w * eligible_count
                power_piece_w = min(
                    remaining_export_w,
                    max(natural_surplus_w - controller_power_ceiling_w, 0.0),
                )
                remaining_export_w -= power_piece_w
                commanded_charge_w = _mask_count(controller_state.mask) * controller_state.rate_w
                acceptance_shortfall_w = max(commanded_charge_w - actual_charge_w, 0.0)
                capacity_piece_w = min(remaining_export_w, acceptance_shortfall_w)
            control_piece_w = max(export_w - power_piece_w - capacity_piece_w, 0.0)
            capacity_limited_export_kwh += capacity_piece_w / 1000.0 * dt_h
            power_limited_export_kwh += power_piece_w / 1000.0 * dt_h
            control_limited_export_kwh += control_piece_w / 1000.0 * dt_h

        if not ignore_battery_capacity:
            for index, accepted in enumerate(accepted_ac_by_bank):
                stored_gain = accepted * charge_efficiency
                stored[index] = min(stored[index] + stored_gain, charge_ceiling[index])
                stored_charge_kwh += stored_gain
        else:
            stored_charge_kwh += accepted_ac_kwh * charge_efficiency

        elapsed_h += dt_h

    ending_socs = tuple(
        100.0 * stored[index] / capacities[index]
        for index in range(3)
    )
    total_capacity = sum(capacities)
    aggregate_soc = 100.0 * sum(stored) / total_capacity

    return SimulationResult(
        ending_bank_socs_pct=ending_socs,
        aggregate_soc_pct=aggregate_soc,
        stored_charge_kwh=stored_charge_kwh,
        battery_ac_charge_kwh=battery_ac_charge_kwh,
        solar_to_battery_ac_kwh=solar_to_battery_ac_kwh,
        grid_to_battery_ac_kwh=grid_to_battery_ac_kwh,
        predicted_export_kwh=predicted_export_kwh,
        predicted_grid_import_kwh=predicted_grid_import_kwh,
        capacity_limited_export_kwh=capacity_limited_export_kwh,
        power_limited_export_kwh=power_limited_export_kwh,
        control_limited_export_kwh=control_limited_export_kwh,
        peak_export_w=peak_export_w,
        export_minutes=export_minutes,
        average_load_kw=average_load_kw,
        model=f"controller_mirror_triangle_v1:{controller.source}",
    )
