from __future__ import annotations

from typing import Any

from .const import (
    CONF_CAPACITY_KWH,
    CONF_SOC_WEIGHTS,
    OPT_AUTO_BATTERY_TOPOLOGY,
)

LEGACY_TOPOLOGY_KEYS = (
    CONF_CAPACITY_KWH,
    CONF_SOC_WEIGHTS,
    OPT_AUTO_BATTERY_TOPOLOGY,
)


def remove_legacy_topology_keys(
    data: dict[str, Any],
    options: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Remove topology inputs that are no longer part of live configuration."""
    migrated_data = dict(data)
    migrated_options = dict(options)
    changed = False

    for key in LEGACY_TOPOLOGY_KEYS:
        if key in migrated_data:
            migrated_data.pop(key, None)
            changed = True
        if key in migrated_options:
            migrated_options.pop(key, None)
            changed = True

    return migrated_data, migrated_options, changed
