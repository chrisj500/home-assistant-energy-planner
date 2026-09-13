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
    surplus_next_3d_kwh: float = 0.0,
    surplus_horizon_kwh: float = 0.0,
) -> EvChargingOutlook:
    """Return a simple green/yellow/red strategic EV charging recommendation.

    The traffic light is intentionally broader than the preferred charge window:
    green means EV charging is strategically useful because material solar
    headroom pressure is approaching within roughly two days; yellow means the
    outlook is mixed or the need is farther away; red means no material headroom
    pressure is forecast and the rolling solar outlook is weak enough that grid
    energy is likely to dominate discretionary charging.
    """
    fraction = None
    if best_solar_fraction is not None:
        fraction = min(max(float(best_solar_fraction), 0.0), 1.0)
    next_3d = max(float(surplus_next_3d_kwh), 0.0)
    horizon = max(float(surplus_horizon_kwh), 0.0)

    if risk_date is not None:
        days = max((risk_date - reference_date).days, 0)
        if days <= 2:
            solar_text = (
                f" Preferred EV window is about {fraction * 100:.0f}% solar."
                if fraction is not None
                else ""
            )
            return EvChargingOutlook(
                status="green",
                reason=(
                    f"Stationary-battery headroom pressure is forecast in {days} day(s); "
                    "using the EV as a flexible load is strategically useful."
                    f"{solar_text}"
                ),
                days_to_risk=days,
            )
        return EvChargingOutlook(
            status="yellow",
            reason=(
                f"A stationary-battery headroom risk is forecast in {days} day(s), but "
                "the need is not immediate. Preserve some EV flexibility for later "
                "solar-rich windows."
            ),
            days_to_risk=days,
        )

    if next_3d >= 5.0 or horizon >= 10.0 or (fraction is not None and fraction >= 0.40):
        details = []
        if next_3d > 0:
            details.append(f"about {next_3d:.1f} kWh of modeled solar surplus in the next 3 days")
        if fraction is not None:
            details.append(f"best EV window about {fraction * 100:.0f}% solar")
        suffix = "; ".join(details) if details else "some usable solar opportunity remains"
        return EvChargingOutlook(
            status="yellow",
            reason=(
                "No material stationary-battery headroom risk is forecast, so charging "
                f"is optional rather than urgent; {suffix}."
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
            "No material stationary-battery headroom risk is forecast and the rolling "
            f"solar outlook is weak for discretionary EV charging. {solar_text}"
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
