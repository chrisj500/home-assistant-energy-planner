from pathlib import Path
import sys

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from config_migration import remove_legacy_topology_keys  # noqa: E402


def test_legacy_topology_keys_are_removed_without_touching_other_settings():
    data = {
        "capacity_kwh": 49.152,
        "soc_weights": "3,2,3",
        "soc_entity_1": "sensor.one",
        "charge_limit_entity": "number.limit",
    }
    options = {
        "capacity_kwh": 49.152,
        "soc_weights": "3,2,3",
        "auto_battery_topology": True,
        "minimum_reserve": 20.0,
    }

    migrated_data, migrated_options, changed = remove_legacy_topology_keys(
        data,
        options,
    )

    assert changed is True
    assert "capacity_kwh" not in migrated_data
    assert "soc_weights" not in migrated_data
    assert "capacity_kwh" not in migrated_options
    assert "soc_weights" not in migrated_options
    assert "auto_battery_topology" not in migrated_options
    assert migrated_data["soc_entity_1"] == "sensor.one"
    assert migrated_data["charge_limit_entity"] == "number.limit"
    assert migrated_options["minimum_reserve"] == 20.0


def test_clean_entry_is_idempotent():
    data = {"soc_entity_1": "sensor.one"}
    options = {"minimum_reserve": 20.0}

    migrated_data, migrated_options, changed = remove_legacy_topology_keys(
        data,
        options,
    )

    assert changed is False
    assert migrated_data == data
    assert migrated_options == options
