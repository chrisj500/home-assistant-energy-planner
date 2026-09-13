from __future__ import annotations

from dataclasses import dataclass
from math import fsum
from statistics import median
from typing import Iterable, Mapping, Sequence


MIN_ACTION_SAMPLES = 3
CALIBRATED_SAMPLES = 10
MAX_HISTORY_SAMPLES = 30


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    """Evidence-derived uncertainty profile for headroom decisions.

    Daylight error is actual stored-energy gain minus predicted stored-energy
    gain. Negative values mean the point forecast over-predicted how much solar
    energy would actually be stored.

    Overnight samples are measured stored-energy drop rates. For an intentional
    headroom action we use the *upper* empirical overnight depletion bound. That
    is deliberate: natural overnight discharge creates headroom for free, so we
    only recommend extra discharge that remains necessary even on a night that
    naturally creates relatively more headroom. Combined with a lower empirical
    daylight-storage bound, this implements a no-regret bias against buying
    energy later because of an overconfident forecast.
    """

    status: str
    action_ready: bool
    daylight_samples: int
    overnight_samples: int
    daylight_lower_error_ratio: float
    daylight_mae_kwh: float
    daylight_mae_ratio: float
    headroom_factor: float
    overnight_lower_drop_kw: float
    overnight_median_drop_kw: float
    overnight_upper_drop_kw: float


@dataclass(frozen=True, slots=True)
class HeadroomDecision:
    nominal_required_headroom_kwh: float
    confidence_required_headroom_kwh: float
    conservative_available_headroom_kwh: float
    confidence_shortfall_kwh: float
    recommended_additional_discharge_kwh: float
    action_ready: bool
    reason: str


def _float_values(values: Iterable[object]) -> list[float]:
    result: list[float] = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def quantile(values: Sequence[float], q: float) -> float:
    """Return a linearly interpolated empirical quantile."""
    data = sorted(float(value) for value in values)
    if not data:
        raise ValueError("quantile requires at least one value")
    q = min(max(float(q), 0.0), 1.0)
    if len(data) == 1:
        return data[0]
    position = (len(data) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(data) - 1)
    fraction = position - lower
    return data[lower] * (1.0 - fraction) + data[upper] * fraction


def _lower_empirical_bound(values: Sequence[float], minimum_samples: int) -> float:
    if len(values) < minimum_samples:
        return 0.0
    if len(values) < CALIBRATED_SAMPLES:
        return min(values)
    return quantile(values, 0.10)


def _upper_empirical_bound(values: Sequence[float], minimum_samples: int) -> float:
    if len(values) < minimum_samples:
        return 0.0
    if len(values) < CALIBRATED_SAMPLES:
        return max(values)
    return quantile(values, 0.90)


def build_profile(
    daylight_records: Iterable[Mapping[str, object]],
    overnight_records: Iterable[Mapping[str, object]],
) -> CalibrationProfile:
    daylight = list(daylight_records)[-MAX_HISTORY_SAMPLES:]
    overnight = list(overnight_records)[-MAX_HISTORY_SAMPLES:]

    daylight_errors = _float_values(record.get("error_kwh") for record in daylight)
    daylight_ratios = _float_values(record.get("error_ratio") for record in daylight)
    overnight_rates = [
        max(value, 0.0)
        for value in _float_values(record.get("drop_rate_kw") for record in overnight)
    ]

    daylight_count = min(len(daylight_errors), len(daylight_ratios))
    overnight_count = len(overnight_rates)
    daylight_errors = daylight_errors[-daylight_count:]
    daylight_ratios = daylight_ratios[-daylight_count:]

    if daylight_count:
        daylight_mae_kwh = fsum(abs(value) for value in daylight_errors) / daylight_count
        daylight_mae_ratio = fsum(abs(value) for value in daylight_ratios) / daylight_count
    else:
        daylight_mae_kwh = 0.0
        daylight_mae_ratio = 0.0

    lower_ratio = _lower_empirical_bound(daylight_ratios, MIN_ACTION_SAMPLES)
    lower_ratio = min(lower_ratio, 0.0)
    headroom_factor = min(max(1.0 + lower_ratio, 0.0), 1.0)

    overnight_lower = max(
        _lower_empirical_bound(overnight_rates, MIN_ACTION_SAMPLES),
        0.0,
    )
    overnight_upper = max(
        _upper_empirical_bound(overnight_rates, MIN_ACTION_SAMPLES),
        0.0,
    )
    overnight_median = median(overnight_rates) if overnight_rates else 0.0

    action_ready = (
        daylight_count >= MIN_ACTION_SAMPLES
        and overnight_count >= MIN_ACTION_SAMPLES
    )
    if not action_ready:
        status = "learning"
    elif daylight_count < CALIBRATED_SAMPLES or overnight_count < CALIBRATED_SAMPLES:
        status = "calibrating"
    else:
        status = "calibrated"

    return CalibrationProfile(
        status=status,
        action_ready=action_ready,
        daylight_samples=daylight_count,
        overnight_samples=overnight_count,
        daylight_lower_error_ratio=lower_ratio,
        daylight_mae_kwh=daylight_mae_kwh,
        daylight_mae_ratio=daylight_mae_ratio,
        headroom_factor=headroom_factor,
        overnight_lower_drop_kw=overnight_lower,
        overnight_median_drop_kw=overnight_median,
        overnight_upper_drop_kw=overnight_upper,
    )


def append_record(
    records: list[dict[str, object]],
    record: Mapping[str, object],
    *,
    limit: int = MAX_HISTORY_SAMPLES,
) -> list[dict[str, object]]:
    updated = [*records, dict(record)]
    return updated[-max(int(limit), 1):]


def projected_overnight_drop_kwh(
    *,
    drop_kw: float,
    night_hours: float,
    sunset_soc_pct: float,
    capacity_kwh: float,
    reserve_pct: float,
) -> float:
    """Return a bounded natural overnight stored-energy drop estimate."""
    capacity_kwh = max(float(capacity_kwh), 0.001)
    sunset_soc_pct = min(max(float(sunset_soc_pct), 0.0), 100.0)
    reserve_pct = min(max(float(reserve_pct), 0.0), 100.0)
    available_kwh = max(
        capacity_kwh * (sunset_soc_pct - reserve_pct) / 100.0,
        0.0,
    )
    expected_drop = max(float(drop_kw), 0.0) * max(float(night_hours), 0.0)
    return min(expected_drop, available_kwh)


def apply_stored_energy_drop(
    *,
    bank_socs_pct: tuple[float, float, float],
    bank_capacities_kwh: tuple[float, float, float],
    drop_kwh: float,
    reserve_pct: float,
) -> tuple[float, float, float]:
    """Reduce bank SOCs proportionally above a common reserve floor."""
    capacities = tuple(max(float(value), 0.001) for value in bank_capacities_kwh)
    socs = tuple(min(max(float(value), 0.0), 100.0) for value in bank_socs_pct)
    stored = [capacity * soc / 100.0 for capacity, soc in zip(capacities, socs)]
    floors = [capacity * reserve_pct / 100.0 for capacity in capacities]
    available = [max(value - floor, 0.0) for value, floor in zip(stored, floors)]
    total_available = sum(available)
    if total_available <= 0 or drop_kwh <= 0:
        return socs

    fraction = min(float(drop_kwh) / total_available, 1.0)
    ending = [
        value - available_energy * fraction
        for value, available_energy in zip(stored, available)
    ]
    return tuple(
        100.0 * value / capacity
        for value, capacity in zip(ending, capacities)
    )


def confidence_headroom_decision(
    *,
    profile: CalibrationProfile,
    nominal_required_headroom_kwh: float,
    conservative_available_headroom_kwh: float,
    stored_above_reserve_kwh: float,
) -> HeadroomDecision:
    """Turn a physical point forecast into an evidence-backed action amount."""
    nominal_required = max(float(nominal_required_headroom_kwh), 0.0)
    conservative_available = max(float(conservative_available_headroom_kwh), 0.0)
    stored_above_reserve = max(float(stored_above_reserve_kwh), 0.0)

    if not profile.action_ready:
        # Keep the physical point-forecast risk visible while explicitly gating
        # action. A learning status should never make a real nominal shortfall
        # disappear from diagnostics merely because confidence is not ready.
        provisional_shortfall = max(nominal_required - conservative_available, 0.0)
        reason = (
            "Forecast calibration is still learning. Preserve stored solar; "
            f"automatic headroom action requires at least {MIN_ACTION_SAMPLES} "
            "valid daylight and overnight observations."
        )
        return HeadroomDecision(
            nominal_required_headroom_kwh=nominal_required,
            confidence_required_headroom_kwh=nominal_required,
            conservative_available_headroom_kwh=conservative_available,
            confidence_shortfall_kwh=provisional_shortfall,
            recommended_additional_discharge_kwh=0.0,
            action_ready=False,
            reason=reason,
        )

    confidence_required = nominal_required * profile.headroom_factor
    shortfall = max(confidence_required - conservative_available, 0.0)
    recommended = min(shortfall, stored_above_reserve)
    reason = (
        f"No-regret stored-solar need is {confidence_required:.2f} kWh "
        f"({profile.headroom_factor * 100:.1f}% of the physical point forecast) "
        f"versus {conservative_available:.2f} kWh of headroom after the upper "
        "empirical natural-overnight-depletion allowance."
    )
    return HeadroomDecision(
        nominal_required_headroom_kwh=nominal_required,
        confidence_required_headroom_kwh=confidence_required,
        conservative_available_headroom_kwh=conservative_available,
        confidence_shortfall_kwh=shortfall,
        recommended_additional_discharge_kwh=recommended,
        action_ready=True,
        reason=reason,
    )
