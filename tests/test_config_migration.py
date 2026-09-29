import ast
from pathlib import Path
import sys

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from config_migration import (  # noqa: E402
    CONFIG_ENTRY_VERSION,
    remove_legacy_topology_keys,
)


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


def test_home_assistant_migration_handler_is_module_level():
    integration_tree = ast.parse((MODULE_DIR / "__init__.py").read_text())
    module_handlers = [
        node
        for node in integration_tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "async_migrate_entry"
    ]
    assert len(module_handlers) == 1

    flow_tree = ast.parse((MODULE_DIR / "config_flow.py").read_text())
    misplaced_handlers = [
        child
        for node in flow_tree.body
        if isinstance(node, ast.ClassDef)
        for child in node.body
        if isinstance(child, ast.AsyncFunctionDef)
        and child.name == "async_migrate_entry"
    ]
    assert misplaced_handlers == []


def test_config_flow_uses_shared_entry_version():
    assert CONFIG_ENTRY_VERSION == 2
    source = (MODULE_DIR / "config_flow.py").read_text()
    assert "VERSION = CONFIG_ENTRY_VERSION" in source
