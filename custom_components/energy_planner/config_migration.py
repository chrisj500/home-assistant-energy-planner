from __future__ import annotations

from typing import Any

# Literal legacy keys keep this pure migration helper independent of Home
# Assistant/package imports and document exactly what is removed.
CONFIG_ENTRY_VERSION = 2

LEGACY_TOPOLOGY_KEYS = (
    "capacity_kwh",
    "soc_weights",
    "auto_battery_topology",
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
