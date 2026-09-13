from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class EvChargingOutlook:
    status: str
    reason: str
    days_to_risk: int | None


def classify_ev_charging_outlook(
    *,
    reference_date: date,
    risk_date: date | None,
    best_solar_fraction: float | None,
) -> EvChargingOutlook:
    """Return a simple green/yellow/red EV charging recommendation.

    Green means charging is strategically useful because a material stationary-
    battery headroom problem is approaching within roughly two days and a strong
    solar-rich EV window exists. Yellow means charging is acceptable but timing
    matters. Red means there is no modeled headroom need and the best available
    solar opportunity is weak enough that charging is likely to lean on the grid.
    """
    fraction = None
    if best_solar_fraction is not None:
        fraction = min(max(float(best_solar_fraction), 0.0), 1.0)

    if risk_date is not None:
        days = max((risk_date - reference_date).days, 0)
        if days <= 2 and fraction is not None and fraction >= 0.80:
            return EvChargingOutlook(
                status="green",
                reason=(
                    f"Stationary-battery headroom pressure is forecast in {days} day(s), "
                    f"and the preferred EV window is about {fraction * 100:.0f}% solar."
                ),
                days_to_risk=days,
            )
        if days <= 2:
            solar_text = (
                f"The best EV window is only about {fraction * 100:.0f}% solar."
                if fraction is not None
                else "No strong solar-rich EV window is currently available."
            )
            return EvChargingOutlook(
                status="yellow",
                reason=(
                    f"Headroom pressure is forecast within {days} day(s), but charging "
                    f"is constrained. {solar_text}"
                ),
                days_to_risk=days,
            )
        return EvChargingOutlook(
            status="yellow",
            reason=(
                f"A stationary-battery headroom risk is forecast in {days} day(s). "
                "Charging can be useful, but preserving EV flexibility for a better "
                "solar window is preferred."
            ),
            days_to_risk=days,
        )

    if fraction is not None and fraction >= 0.40:
        return EvChargingOutlook(
            status="yellow",
            reason=(
                "No material stationary-battery headroom risk is forecast, but a mixed "
                f"solar charging opportunity exists at about {fraction * 100:.0f}% solar."
            ),
            days_to_risk=None,
        )

    solar_text = (
        f"The best modeled EV opportunity is only about {fraction * 100:.0f}% solar."
        if fraction is not None
        else "No useful solar-rich EV window is available in the rolling forecast."
    )
    return EvChargingOutlook(
        status="red",
        reason=(
            "No material stationary-battery headroom risk is forecast and the solar "
            f"outlook is weak for EV charging. {solar_text}"
        ),
        days_to_risk=None,
    )


def ev_soc_data_status(age_minutes: float | None, *, charging: bool) -> str:
    """Describe how fresh the EV SOC telemetry is."""
    if age_minutes is None:
        return "unavailable"
    age = max(float(age_minutes), 0.0)
    if age <= 30.0:
        return "fresh"
    if charging:
        return "charging_soc_stale"
    if age <= 120.0:
        return "aging"
    return "stale"


def auto_charge_eligibility(
    *,
    outlook_status: str,
    now: datetime,
    window_start: datetime | None,
    window_end: datetime | None,
    ev_home: bool | None,
    available_energy_kwh: float | None,
    solar_fraction: float | None,
) -> tuple[bool, str]:
    """Expose planner eligibility for a future Smart Panel EV executor.

    This does not actuate hardware. A future executor must independently confirm
    that the vehicle is plugged in and that the Smart Panel circuit is healthy.
    """
    if ev_home is False:
        return False, "EV is away."
    if available_energy_kwh is None or available_energy_kwh <= 0.05:
        return False, "EV energy-to-target is unavailable or already satisfied."
    if outlook_status != "green":
        return False, f"EV charging outlook is {outlook_status}, not green."
    if window_start is None or window_end is None:
        return False, "No preferred EV charging window is available."
    if not (window_start <= now <= window_end):
        return False, "Waiting for the preferred EV charging window."
    if solar_fraction is None or solar_fraction < 0.80:
        return False, "Preferred EV window is not sufficiently solar supplied."
    return True, "Green outlook and preferred solar charging window are active."
