"""Regression guards for restart-safe Forecast.Solar interval caching."""
import ast
from pathlib import Path
import unittest


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "energy_planner"
    / "enhanced_coordinator.py"
)


def method(name):
    tree = ast.parse(SOURCE.read_text())
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "EnhancedEnergyPlannerCoordinator"
    )
    return next(
        node for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


class ForecastCacheTests(unittest.TestCase):
    def test_transient_refresh_failure_does_not_clear_last_good_payload(self):
        refresh = method("_refresh_sidecar")
        clears = []
        for node in ast.walk(refresh):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "_estimate_payload"
                    and isinstance(value, ast.Constant)
                    and value.value is None
                ):
                    clears.append(node.lineno)
        self.assertEqual(
            clears,
            [],
            "A transient Forecast.Solar refresh must not erase the last good interval payload",
        )

    def test_successful_refresh_persists_and_startup_restores_cache(self):
        refresh = method("_refresh_sidecar")
        shadow = method("_forecast_solar_shadow")
        refresh_calls = {
            node.func.attr
            for node in ast.walk(refresh)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        shadow_calls = {
            node.func.attr
            for node in ast.walk(shadow)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("_save_estimate_cache", refresh_calls)
        self.assertIn("_restore_estimate_cache", shadow_calls)


if __name__ == "__main__":
    unittest.main()
