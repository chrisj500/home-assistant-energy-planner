from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(float(value), minimum), maximum)


@dataclass(frozen=True, slots=True)
class ExportDefenseAssessment:
    """Forecast-module assessment for the zero-export objective."""

    risk: bool
    headroom_kwh: float
    direct_headroom_kwh: float
    uncertainty_headroom_kwh: float
    risk_adjusted_ceiling_pct: float
    nominal_sunset_soc_pct: float
    defense_sunset_soc_pct: float
    nominal_export_kwh: float
    defense_export_kwh: float
    reason: str


def assess_export_defense(
    *,
    nominal,
    defense,
    capacity_kwh: float,
    charge_limit_pct: float,
    forecast_width_soc: float,
    charge_efficiency: float,
    threshold_kwh: float = 0.25,
) -> ExportDefenseAssessment:
    """Return headroom needed to protect against export, not grid purchases.

    The point forecast remains the nominal estimate. Export defense is a separate
    planning envelope. We protect against two ways solar can be lost:

    1. The simulated high-solar / low-load case directly runs out of storage
       capacity and exports.
    2. The point or defense sunset SOC lands inside the empirically observed
       forecast-error band below the configured charge ceiling.

    The larger of those requirements becomes the forecast-module headroom need.
    No "safety margin" is subtracted: for this planner, a false negative that
    causes solar export is more costly than creating somewhat too much headroom.
    """
    capacity = max(float(capacity_kwh), 0.001)
    limit = _clamp(charge_limit_pct, 0.0, 100.0)
    width = _clamp(forecast_width_soc, 0.0, 100.0)
    efficiency = _clamp(charge_efficiency, 0.0, 1.0)
    threshold = max(float(threshold_kwh), 0.0)

    nominal_soc = float(nominal.end_soc_pct)
    defense_soc = float(defense.end_soc_pct)
    nominal_export = max(float(nominal.capacity_export_kwh), 0.0)
    defense_export = max(float(defense.capacity_export_kwh), 0.0)

    direct = max(
        float(nominal.headroom_shortfall_kwh),
        nominal_export * efficiency,
        float(defense.headroom_shortfall_kwh),
        defense_export * efficiency,
        0.0,
    )

    risk_ceiling = _clamp(limit - width, 0.0, limit)
    defended_soc = max(nominal_soc, defense_soc)
    uncertainty = max(defended_soc - risk_ceiling, 0.0) / 100.0 * capacity

    headroom = max(direct, uncertainty)
    at_ceiling = (
        nominal_soc >= limit - 0.25
        or defense_soc >= limit - 0.25
    )
    risk = headroom >= threshold or at_ceiling
    if risk and headroom < threshold:
        headroom = threshold

    if defense_export > 0.0 or float(defense.headroom_shortfall_kwh) > 0.0:
        reason = "export_defense_capacity_shortfall"
    elif nominal_export > 0.0 or float(nominal.headroom_shortfall_kwh) > 0.0:
        reason = "nominal_capacity_shortfall"
    elif at_ceiling:
        reason = "projected_saturation"
    elif uncertainty > 0.0:
        reason = "forecast_error_band_reaches_charge_ceiling"
    else:
        reason = "clear"

    return ExportDefenseAssessment(
        risk=risk,
        headroom_kwh=headroom,
        direct_headroom_kwh=direct,
        uncertainty_headroom_kwh=uncertainty,
        risk_adjusted_ceiling_pct=risk_ceiling,
        nominal_sunset_soc_pct=nominal_soc,
        defense_sunset_soc_pct=defense_soc,
        nominal_export_kwh=nominal_export,
        defense_export_kwh=defense_export,
        reason=reason,
    )
