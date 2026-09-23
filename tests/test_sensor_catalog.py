from __future__ import annotations

import ast
from pathlib import Path
import unittest


SENSOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "energy_planner"
    / "sensor.py"
)


class SensorCatalogTests(unittest.TestCase):
    def test_battery_flow_outputs_are_registered_as_entities(self) -> None:
        tree = ast.parse(SENSOR_PATH.read_text(encoding="utf-8"))
        sensors = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SENSORS"
                for target in node.targets
            )
        )
        self.assertIsInstance(sensors, ast.Tuple)

        descriptions: dict[str, tuple[str, str]] = {}
        for item in sensors.elts:
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name) and item.func.id == "_total_energy":
                key, data_key, name = (
                    ast.literal_eval(argument) for argument in item.args[:3]
                )
                descriptions[key] = (data_key, name)
                continue

            if (
                isinstance(item.func, ast.Name)
                and item.func.id == "EnergyPlannerSensorDescription"
            ):
                values = {
                    keyword.arg: ast.literal_eval(keyword.value)
                    for keyword in item.keywords
                    if keyword.arg in {"key", "data_key", "name"}
                }
                if "key" in values:
                    descriptions[values["key"]] = (
                        values["data_key"],
                        values["name"],
                    )

        self.assertEqual(
            descriptions["battery_bank_power"],
            ("battery_bank_power_w", "Battery Bank Power"),
        )
        self.assertEqual(
            descriptions["battery_energy_charged"],
            ("battery_energy_charged_kwh", "Battery Energy Charged"),
        )
        self.assertEqual(
            descriptions["battery_energy_discharged"],
            ("battery_energy_discharged_kwh", "Battery Energy Discharged"),
        )
        self.assertEqual(
            descriptions["forecast_learning_progress"],
            ("forecast_learning_progress", "Forecast Learning Progress"),
        )
        self.assertEqual(
            descriptions["storm_safety_status"],
            ("storm_safety_status", "Storm Safety Status"),
        )

    def test_existing_entries_can_configure_battery_power_entities(self) -> None:
        config_flow_path = (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "energy_planner"
            / "config_flow.py"
        )
        source = config_flow_path.read_text(encoding="utf-8")

        # Each constant must appear in the import, initial setup schema, and
        # existing-entry options schema. This guards against exposing a field
        # only during first-time installation.
        for key in (
            "CONF_BATTERY_POWER_1",
            "CONF_BATTERY_POWER_2",
            "CONF_BATTERY_POWER_3",
        ):
            self.assertGreaterEqual(source.count(key), 3, key)


if __name__ == "__main__":
    unittest.main()
