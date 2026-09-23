from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from hvac import HVAC_READY_DAYS, HVAC_READY_SAMPLES, celsius, effective_action, hourly_forecast, learning_progress, observe, room_summary


class HVACTests(unittest.TestCase):
    def sample(self, at=1000, **kwargs):
        return dict(at=at, mode="cool", action="cooling", target_c=22,
                    indoor_c=22, outdoor_c=30, outdoor_humidity=60,
                    humidity=60, condenser_w=2000, blower_w=200, **kwargs)

    def test_temperature_conversion_and_invalid_values(self):
        self.assertAlmostEqual(celsius(72, "°F"), 22.222222, places=5)
        for value, unit in [("nan", "°C"), (71, "K"), (500, "°C")]:
            self.assertIsNone(celsius(value, unit))

    def test_effective_action_prefers_thermostat(self):
        self.assertEqual(
            effective_action("cool", "idle", 2000, 200),
            ("idle", "thermostat"),
        )

    def test_effective_action_infers_cool_mode_from_power(self):
        self.assertEqual(
            effective_action("cool", None, 0, 12),
            ("idle", "inferred_power"),
        )
        self.assertEqual(
            effective_action("cool", None, 0, 150),
            ("fan", "inferred_power"),
        )
        self.assertEqual(
            effective_action("cool", None, 2200, 150),
            ("cooling", "inferred_power"),
        )

    def test_effective_action_infers_heat_and_off_conservatively(self):
        self.assertEqual(
            effective_action("heat", None, 0, 150),
            ("heating", "inferred_power"),
        )
        self.assertEqual(
            effective_action("heat", None, 0, 10),
            ("idle", "inferred_power"),
        )
        self.assertEqual(
            effective_action("off", None, None, None),
            ("off", "inferred_mode"),
        )
        self.assertEqual(
            effective_action("heat_cool", None, 0, 150),
            (None, "unsupported_mode"),
        )

    def test_duplicate_bedroom_devices_are_one_room(self):
        summary = room_summary([24, 20, 22, 22], [50, 60, 70, 70])
        self.assertEqual(summary["temperature_c"], 22)
        self.assertEqual(summary["humidity"], 60)
        self.assertEqual(summary["room_count"], 3)

    def test_missing_blower_requires_sustained_unmet_demand(self):
        memory = {}
        for elapsed in [0, 300, 600]:
            sample = self.sample(1000 + elapsed)
            sample.update(indoor_c=25, blower_w=0)
            result = observe(memory, sample)
            self.assertEqual(result["status"], "suspected_fault" if elapsed == 600 else "learning")
        self.assertEqual(memory["samples"], [])

    def test_cooling_missing_condenser(self):
        memory = {}
        for elapsed in [0, 300, 600]:
            sample = self.sample(1000 + elapsed)
            sample.update(indoor_c=25, condenser_w=0)
            result = observe(memory, sample)
        self.assertEqual(result["status"], "suspected_fault")

    def test_gas_heat_does_not_require_condenser(self):
        memory = {}
        for elapsed in [0, 300, 600]:
            sample = self.sample(1000 + elapsed)
            sample.update(mode="heat", action="heating", indoor_c=19, condenser_w=0)
            result = observe(memory, sample)
        self.assertEqual(result["status"], "learning")

    def test_gap_resets_fault_timer(self):
        memory = {}
        for at in [1000, 5000]:
            sample = self.sample(at)
            sample.update(indoor_c=25, blower_w=0)
            result = observe(memory, sample)
        self.assertEqual(result["status"], "learning")

    def test_stale_input_is_not_zero(self):
        sample = self.sample()
        sample["blower_w"] = None
        result = observe({}, sample)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("blower_w", result["input_issues"])

    def test_setpoint_change_resets_response_window(self):
        memory = {}
        for i in range(8):
            sample = self.sample(1000 + i * 300)
            sample.update(indoor_c=25, target_c=22 if i < 5 else 21)
            result = observe(memory, sample)
        self.assertEqual(result["status"], "learning")

    def test_no_temperature_progress_flags_suspected_fault(self):
        memory = {}
        for i in range(7):
            sample = self.sample(1000 + i * 300)
            sample.update(indoor_c=25)
            result = observe(memory, sample)
        self.assertEqual(result["status"], "suspected_fault")

    def test_samples_persist_and_duplicate_poll_does_not_train(self):
        memory = {}
        observe(memory, self.sample())
        observe(memory, self.sample(1300))
        restored = deepcopy(memory)
        observe(restored, self.sample(1300))
        self.assertEqual(len(restored["samples"]), 1)

    def test_live_recovery_estimate_after_ten_minutes(self):
        memory = {}
        first = self.sample(1000)
        first.update(indoor_c=25, target_c=22, outdoor_c=30)
        observe(memory, first)

        second = self.sample(1600)
        second.update(indoor_c=24.5, target_c=22, outdoor_c=30)
        result = observe(memory, second)

        recovery = result["recovery"]
        self.assertTrue(recovery["active"])
        self.assertEqual(recovery["status"], "provisional")
        self.assertEqual(recovery["source"], "live_call")
        self.assertAlmostEqual(recovery["rate_c_per_hour"], 3.0)
        self.assertAlmostEqual(recovery["eta_minutes"], 50.0)

    def test_completed_recovery_cycle_is_retained(self):
        memory = {}
        first = self.sample(1000)
        first.update(indoor_c=25, target_c=22, outdoor_c=30)
        observe(memory, first)

        second = self.sample(1600)
        second.update(indoor_c=24.5, target_c=22, outdoor_c=30)
        observe(memory, second)

        end = self.sample(2200)
        end.update(
            action="idle",
            indoor_c=24,
            target_c=22,
            outdoor_c=30,
            condenser_w=0,
            blower_w=10,
        )
        result = observe(memory, end)

        self.assertFalse(result["recovery"]["active"])
        self.assertEqual(len(memory["recovery_cycles"]), 1)
        self.assertAlmostEqual(
            memory["recovery_cycles"][0]["rate_c_per_hour"],
            3.0,
        )

    def test_passive_thermal_drift_becomes_provisional_after_long_idle_window(self):
        memory = {}
        for i in range(7):
            at = 1000 + i * 300
            indoor = 22.0 - 0.3 * (i / 6)
            sample = self.sample(at)
            sample.update(
                action="idle",
                indoor_c=indoor,
                target_c=22,
                outdoor_c=10,
                condenser_w=0,
                blower_w=10,
            )
            result = observe(memory, sample)

        thermal = result["thermal"]
        self.assertEqual(thermal["status"], "provisional")
        self.assertEqual(thermal["source"], "live_window")
        self.assertIsNotNone(thermal["coefficient_per_hour"])
        self.assertLess(thermal["predicted_drift_c_per_hour"], 0)
        self.assertGreater(thermal["time_constant_hours"], 0)

    def test_passive_thermal_history_requires_three_completed_windows(self):
        memory = {}
        start = 1000
        for window in range(3):
            base = start + window * 7200
            for i in range(7):
                at = base + i * 300
                indoor = 22.0 - 0.3 * (i / 6)
                sample = self.sample(at)
                sample.update(
                    action="idle",
                    indoor_c=indoor,
                    target_c=22,
                    outdoor_c=10,
                    condenser_w=0,
                    blower_w=10,
                )
                observe(memory, sample)
            active = self.sample(base + 2100)
            active.update(
                action="cooling",
                indoor_c=21.7,
                target_c=21,
                outdoor_c=10,
                condenser_w=2000,
                blower_w=200,
            )
            observe(memory, active)

        idle = self.sample(start + 3 * 7200)
        idle.update(
            action="idle",
            indoor_c=22,
            target_c=22,
            outdoor_c=10,
            condenser_w=0,
            blower_w=10,
        )
        result = observe(memory, idle)
        self.assertEqual(len(memory["thermal_samples"]), 3)
        self.assertEqual(result["thermal"]["status"], "ready")
        self.assertEqual(result["thermal"]["source"], "history")

    def test_general_learning_has_explicit_completion_criteria(self):
        rows = []
        for day in range(HVAC_READY_DAYS):
            for i in range(HVAC_READY_SAMPLES // HVAC_READY_DAYS):
                row = self.sample(1000 + day * 86400 + i * 300)
                row.update(day=f"2026-09-{20 + day:02d}", response_c_per_hour=0)
                rows.append(row)
        progress = learning_progress(rows)
        self.assertTrue(progress["learning_ready"])
        self.assertEqual(progress["learning_samples"], HVAC_READY_SAMPLES)
        self.assertEqual(progress["learning_days"], HVAC_READY_DAYS)

        memory = {"samples": rows}
        result = observe(memory, self.sample(rows[-1]["at"] + 300))
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["learning_ready"])

    def test_hourly_forecast_needs_multiple_days_and_conditions(self):
        hours = [{"at": 400000, "temperature_c": 30, "humidity": 60}]
        result = hourly_forecast([], hours, mode="cool", target_c=22, now=400000)
        self.assertIsNone(result[0]["expected_w"])
        rows = [self.sample(1000 + day * 86400 + i * 300) for day in range(3) for i in range(12)]
        result = hourly_forecast(rows, hours, mode="cool", target_c=22, now=400000)
        self.assertEqual(result[0]["expected_w"], 2200)
        hours[0]["temperature_c"] = 40
        self.assertIsNone(hourly_forecast(rows, hours, mode="cool", target_c=22, now=400000)[0]["expected_w"])

    def test_missing_humidity_and_auto_mode_do_not_invent_forecast(self):
        rows = [self.sample()]
        rows[0]["outdoor_humidity"] = None
        hours = [{"at": 1000, "temperature_c": 30, "humidity": 60}]
        self.assertIsNone(hourly_forecast(rows, hours, mode="cool", target_c=22, now=1000)[0]["expected_w"])
        self.assertEqual(hourly_forecast(rows, hours, mode="heat_cool", target_c=22, now=1000), [])
