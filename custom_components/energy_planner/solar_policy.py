"""Versioned solar policy; no Home Assistant YAML correction is required."""

LIVE_BLEND_MINUTES = 60.0
LEGACY_REMAINING_ENTITY = "sensor.solar_forecast_remaining_today"
RAW_REMAINING_ENTITY = "sensor.energy_production_today_remaining"


def migrated_solar_options(data: dict, options: dict) -> dict | None:
    """Migrate only the known legacy correction, preserving explicit overrides."""
    current = {**data, **options}
    if current.get("solar_remaining_entity") != LEGACY_REMAINING_ENTITY:
        return None
    return {**options, "solar_remaining_entity": RAW_REMAINING_ENTITY}
