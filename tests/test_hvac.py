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
        self.assertEqual(summary["precision_temperature_c"], 22)
        self.assertEqual(summary["physical_temperatures_c"], [24, 20, 22])
        self.assertEqual(summary["humidity"], 60)
        self.assertEqual(summary["room_count"], 3)

    def test_room_precision_signal_uses_physical_room_median(self):
        summary = room_summary([30, 20, 22, 22], [50, 60, 70, 70])
        self.assertEqual(summary["temperature_c"], 24)
        self.assertEqual(summary["precision_temperature_c"], 22)

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

    def test_recovery_rebases_rate_when_homepod_falls_back_to_thermostat(self):
        memory = {}
        first = self.sample(1000)
        first.update(
            indoor_c=23,
            precision_indoor_c=22.8,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, first)

        fallback = self.sample(1300)
        fallback.update(
            indoor_c=22.8,
            precision_indoor_c=None,
            precision_temperature_source=None,
            target_c=22,
            outdoor_c=30,
        )
        recovery = observe(memory, fallback)["recovery"]

        self.assertEqual(recovery["temperature_signal_source"], "thermostat")
        self.assertAlmostEqual(recovery["call_minutes"], 5.0)
        self.assertAlmostEqual(recovery["rate_segment_minutes"], 0.0)
        self.assertEqual(
            memory["recovery_call"]["rate_movement_source"],
            "thermostat",
        )

    def test_live_recovery_uses_homepod_precision_while_thermostat_is_integer(self):
        memory = {}
        first = self.sample(1000)
        first.update(
            indoor_c=23,
            precision_indoor_c=22.8,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, first)

        second = self.sample(1600)
        second.update(
            indoor_c=23,
            precision_indoor_c=22.4,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
        )
        recovery = observe(memory, second)["recovery"]

        self.assertEqual(recovery["source"], "live_call")
        self.assertAlmostEqual(recovery["rate_c_per_hour"], 2.4)
        self.assertAlmostEqual(recovery["eta_raw_minutes"], 25.0)
        self.assertEqual(
            recovery["temperature_signal_source"],
            "homepod_physical_room_median",
        )
        self.assertAlmostEqual(recovery["temperature_signal_c"], 22.4)
        self.assertAlmostEqual(recovery["thermostat_temperature_c"], 23)

    def test_completed_recovery_cycle_uses_precision_rate_but_thermostat_weather_delta(self):
        memory = {}
        first = self.sample(1000)
        first.update(
            indoor_c=23,
            precision_indoor_c=22.8,
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, first)

        middle = self.sample(1600)
        middle.update(
            indoor_c=23,
            precision_indoor_c=22.4,
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, middle)

        target = self.sample(2200)
        target.update(
            action="cooling",
            indoor_c=22,
            precision_indoor_c=22.0,
            target_c=22,
            outdoor_c=30,
            condenser_w=1500,
            blower_w=300,
        )
        result = observe(memory, target)

        cycle = memory["recovery_cycles"][0]
        self.assertTrue(result["recovery"]["overrun"]["active"])
        self.assertEqual(cycle["phase_end"], "target_reached")
        self.assertAlmostEqual(cycle["rate_c_per_hour"], 2.4)
        self.assertAlmostEqual(cycle["outdoor_delta_c"], 7.5)
        self.assertEqual(
            cycle["temperature_signal_source"],
            "homepod_physical_room_median",
        )

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

    def test_recovery_eta_counts_down_without_temperature_change(self):
        memory = {
            "recovery_cycles": [{
                "ended_at": 900,
                "action": "cooling",
                "rate_c_per_hour": 1.0,
                "outdoor_delta_c": 7,
                "duration_minutes": 60,
            }]
        }
        first = self.sample(1000)
        first.update(indoor_c=23, target_c=22, outdoor_c=30)
        first_result = observe(memory, first)["recovery"]

        second = self.sample(1060)
        second.update(indoor_c=23, target_c=22, outdoor_c=30)
        second_result = observe(memory, second)["recovery"]

        self.assertAlmostEqual(first_result["eta_minutes"], 60.0)
        self.assertAlmostEqual(second_result["eta_minutes"], 59.0)
        self.assertEqual(
            first_result["eta_target_at"],
            second_result["eta_target_at"],
        )
        self.assertEqual(second_result["eta_method"], "target_time_countdown")

    def test_thermostat_rounding_does_not_double_eta(self):
        memory = {
            "recovery_cycles": [{
                "ended_at": 900,
                "action": "cooling",
                "rate_c_per_hour": 1.0,
                "outdoor_delta_c": 7,
                "duration_minutes": 60,
            }]
        }
        first = self.sample(1000)
        first.update(indoor_c=23, target_c=22, outdoor_c=30)
        observe(memory, first)

        rounded_up = self.sample(1060)
        rounded_up.update(indoor_c=23.5, target_c=22, outdoor_c=30)
        recovery = observe(memory, rounded_up)["recovery"]

        self.assertAlmostEqual(recovery["eta_minutes"], 59.0)
        self.assertAlmostEqual(recovery["eta_raw_minutes"], 90.0)
        self.assertEqual(recovery["eta_correction_reason"], "initial_estimate")

    def test_live_rate_materially_corrects_target_time(self):
        memory = {
            "recovery_cycles": [{
                "ended_at": 900,
                "action": "cooling",
                "rate_c_per_hour": 1.0,
                "outdoor_delta_c": 7,
                "duration_minutes": 60,
            }]
        }
        first = self.sample(1000)
        first.update(indoor_c=23, target_c=22, outdoor_c=30)
        observe(memory, first)

        second = self.sample(1600)
        second.update(indoor_c=22.5, target_c=22, outdoor_c=30)
        recovery = observe(memory, second)["recovery"]

        self.assertAlmostEqual(recovery["rate_c_per_hour"], 3.0)
        self.assertAlmostEqual(recovery["eta_raw_minutes"], 10.0)
        self.assertAlmostEqual(recovery["eta_minutes"], 10.0)
        self.assertEqual(recovery["eta_correction_reason"], "live_rate_correction")
        self.assertAlmostEqual(recovery["eta_target_at"], 2200.0)

    def test_brief_restart_resumes_recovery_call_and_counts_elapsed_time(self):
        memory = {}
        first = self.sample(1000)
        first.update(
            indoor_c=23,
            precision_indoor_c=22.8,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, first)

        # Simulate persisted state being loaded after a 60-second HA restart.
        memory["_restart_pending"] = True
        second = self.sample(1060)
        second.update(
            indoor_c=23,
            precision_indoor_c=22.7,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
        )
        result = observe(memory, second)

        self.assertAlmostEqual(result["recovery"]["call_minutes"], 1.0)
        self.assertEqual(
            result["restart_resume"]["status"],
            "resumed_full_continuity",
        )
        self.assertTrue(result["restart_resume"]["resumed_recovery_call"])
        self.assertFalse(result["restart_resume"]["rate_learning_rebased"])

    def test_repeated_brief_restarts_keep_same_recovery_target_time(self):
        memory = {
            "recovery_cycles": [{
                "ended_at": 900,
                "action": "cooling",
                "rate_c_per_hour": 1.0,
                "outdoor_delta_c": 7,
                "duration_minutes": 60,
            }]
        }
        first = self.sample(1000)
        first.update(indoor_c=23, target_c=22, outdoor_c=30)
        observe(memory, first)
        target_at = memory["recovery_eta"]["target_at"]

        for at in (1060, 1120, 1180):
            memory = deepcopy(memory)
            memory["_restart_pending"] = True
            sample = self.sample(at)
            sample.update(indoor_c=23, target_c=22, outdoor_c=30)
            result = observe(memory, sample)
            self.assertEqual(
                result["restart_resume"]["status"],
                "resumed_full_continuity",
            )
            self.assertAlmostEqual(
                result["recovery"]["eta_target_at"],
                target_at,
            )

        self.assertAlmostEqual(result["recovery"]["call_minutes"], 3.0)
        self.assertAlmostEqual(result["recovery"]["eta_minutes"], 57.0)

    def test_long_restart_preserves_logical_call_but_rebases_rate_learning(self):
        memory = {}
        first = self.sample(1000)
        first.update(
            indoor_c=23,
            precision_indoor_c=22.8,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, first)

        memory["_restart_pending"] = True
        second = self.sample(2200)
        second.update(
            indoor_c=23,
            precision_indoor_c=22.5,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
        )
        result = observe(memory, second)

        self.assertAlmostEqual(result["recovery"]["call_minutes"], 20.0)
        self.assertAlmostEqual(result["recovery"]["rate_segment_minutes"], 0.0)
        self.assertEqual(result["recovery"]["source"], "history" if result["recovery"]["matched_cycles"] else "learning")
        self.assertEqual(
            result["restart_resume"]["status"],
            "resumed_logical_rebased_measurements",
        )
        self.assertTrue(result["restart_resume"]["resumed_recovery_call"])
        self.assertTrue(result["restart_resume"]["rate_learning_rebased"])

    def test_restart_with_changed_setpoint_does_not_resume_old_recovery_call(self):
        memory = {}
        first = self.sample(1000)
        first.update(indoor_c=23, target_c=22, outdoor_c=30)
        observe(memory, first)

        memory["_restart_pending"] = True
        changed = self.sample(1060)
        changed.update(indoor_c=23, target_c=21, outdoor_c=30)
        result = observe(memory, changed)

        self.assertEqual(
            result["restart_resume"]["status"],
            "state_changed_or_missing_baseline",
        )
        self.assertAlmostEqual(result["recovery"]["call_minutes"], 0.0)
        self.assertFalse(result["restart_resume"]["resumed_recovery_call"])

    def test_recovery_eta_survives_brief_restart_with_same_target_time(self):
        memory = {
            "recovery_cycles": [{
                "ended_at": 900,
                "action": "cooling",
                "rate_c_per_hour": 1.0,
                "outdoor_delta_c": 7,
                "duration_minutes": 60,
            }]
        }
        first = self.sample(1000)
        first.update(indoor_c=23, target_c=22, outdoor_c=30)
        observe(memory, first)
        self.assertAlmostEqual(
            memory["recovery_eta"]["target_at"],
            4600.0,
        )

        restored = deepcopy(memory)
        restored["_restart_pending"] = True
        after_restart = self.sample(1300)
        after_restart.update(indoor_c=23, target_c=22, outdoor_c=30)
        result = observe(restored, after_restart)
        recovery = result["recovery"]

        self.assertAlmostEqual(recovery["eta_minutes"], 55.0)
        self.assertAlmostEqual(recovery["eta_target_at"], 4600.0)
        self.assertAlmostEqual(recovery["eta_raw_minutes"], 60.0)
        self.assertAlmostEqual(recovery["call_minutes"], 5.0)
        self.assertEqual(
            result["restart_resume"]["status"],
            "resumed_full_continuity",
        )

    def test_recovery_cycle_completes_at_target_before_equipment_stops(self):
        memory = {}
        first = self.sample(1000)
        first.update(indoor_c=25, target_c=22, outdoor_c=30)
        observe(memory, first)

        second = self.sample(1600)
        second.update(indoor_c=24.5, target_c=22, outdoor_c=30)
        observe(memory, second)

        target = self.sample(2200)
        target.update(
            action="cooling",
            indoor_c=22,
            target_c=22,
            outdoor_c=30,
            condenser_w=1500,
            blower_w=300,
        )
        result = observe(memory, target)

        self.assertTrue(result["recovery"]["active"])
        self.assertFalse(result["recovery"]["demand_active"])
        self.assertTrue(result["recovery"]["overrun"]["active"])
        self.assertEqual(len(memory["recovery_cycles"]), 1)
        self.assertEqual(
            memory["recovery_cycles"][0]["phase_end"],
            "target_reached",
        )
        self.assertAlmostEqual(
            memory["recovery_cycles"][0]["rate_c_per_hour"],
            9.0,
        )
        self.assertAlmostEqual(
            result["recovery"]["call_minutes"],
            20.0,
        )
        self.assertAlmostEqual(
            result["recovery"]["equipment_call_minutes"],
            20.0,
        )

    def test_post_target_overrun_does_not_change_recovery_cycle(self):
        memory = {}
        start = self.sample(1000)
        start.update(
            indoor_c=23,
            precision_indoor_c=22.8,
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, start)

        target = self.sample(1600)
        target.update(
            indoor_c=22,
            precision_indoor_c=21.9,
            target_c=22,
            outdoor_c=30,
        )
        target_result = observe(memory, target)
        learned_rate = memory["recovery_cycles"][0]["rate_c_per_hour"]

        overrun = self.sample(2200)
        overrun.update(
            indoor_c=21.5,
            precision_indoor_c=21.2,
            target_c=22,
            outdoor_c=30,
        )
        result = observe(memory, overrun)

        self.assertEqual(len(memory["recovery_cycles"]), 1)
        self.assertAlmostEqual(
            memory["recovery_cycles"][0]["rate_c_per_hour"],
            learned_rate,
        )
        self.assertTrue(result["recovery"]["overrun"]["active"])
        self.assertAlmostEqual(
            result["recovery"]["overrun"]["duration_minutes"],
            10.0,
        )
        self.assertAlmostEqual(
            result["recovery"]["equipment_call_minutes"],
            20.0,
        )
        self.assertGreater(
            result["recovery"]["overrun"]["peak_precision_overshoot_c"],
            0,
        )
        self.assertEqual(target_result["recovery"]["source"], "at_target")

    def test_target_duration_is_retained_when_precision_rate_is_unusable(self):
        memory = {}
        start = self.sample(1000)
        start.update(
            indoor_c=23,
            precision_indoor_c=22.1,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, start)

        target = self.sample(1660)
        target.update(
            indoor_c=22,
            precision_indoor_c=22.0,
            precision_temperature_source="homepod_physical_room_median",
            target_c=22,
            outdoor_c=30,
            condenser_w=1500,
            blower_w=300,
        )
        result = observe(memory, target)

        self.assertEqual(len(memory["recovery_cycles"]), 1)
        cycle = memory["recovery_cycles"][0]
        self.assertEqual(cycle["phase_end"], "target_reached")
        self.assertAlmostEqual(cycle["logical_duration_minutes"], 11.0)
        self.assertAlmostEqual(cycle["start_error_c"], 1.0)
        self.assertIsNone(cycle["rate_c_per_hour"])
        self.assertFalse(cycle["rate_sample_valid"])
        self.assertEqual(
            cycle["rate_rejection_reason"],
            "insufficient_precision_movement",
        )
        model = result["recovery"]["models"]["cooling"]
        self.assertEqual(model["clean_target_samples"], 1)
        self.assertEqual(model["duration_samples"], 1)
        self.assertEqual(model["rate_samples"], 0)
        self.assertEqual(model["usable_eta_samples"], 1)
        self.assertAlmostEqual(model["duration_minutes_per_c"], 11.0)
        self.assertAlmostEqual(
            result["recovery"]["overrun"]["recovery_duration_minutes"],
            11.0,
        )

        stopped = self.sample(1720)
        stopped.update(
            action="idle",
            indoor_c=22,
            precision_indoor_c=22.0,
            target_c=22,
            outdoor_c=30,
            condenser_w=0,
            blower_w=10,
        )
        observe(memory, stopped)

        next_call = self.sample(2000)
        next_call.update(
            indoor_c=23,
            precision_indoor_c=22.1,
            target_c=22,
            outdoor_c=30,
        )
        recovery = observe(memory, next_call)["recovery"]

        self.assertEqual(recovery["source"], "history_duration")
        self.assertAlmostEqual(recovery["eta_raw_minutes"], 11.0)
        self.assertAlmostEqual(recovery["eta_minutes"], 11.0)
        self.assertEqual(recovery["matched_duration_cycles"], 1)
        self.assertEqual(recovery["matched_rate_cycles"], 0)
        self.assertIsNone(recovery["rate_c_per_hour"])

    def test_v060_overrun_backfills_dropped_target_duration_once(self):
        memory = {
            "recovery_cycles": [],
            "overrun_cycles": [],
            "overrun_call": {
                "at": 1660,
                "last_at": 1660,
                "action": "cooling",
                "target_c": 22,
                "call_started_at": 1000,
                "recovery_duration_minutes": 11.0,
                "recovery_rate_c_per_hour": None,
                "started_mid_overrun": False,
                "temperature_signal_source": "homepod_physical_room_median",
            },
        }
        sample = self.sample(1720)
        sample.update(
            indoor_c=22,
            precision_indoor_c=22.0,
            target_c=22,
            outdoor_c=30,
        )

        first = observe(memory, sample)
        second_sample = self.sample(1780)
        second_sample.update(
            indoor_c=22,
            precision_indoor_c=21.9,
            target_c=22,
            outdoor_c=30,
        )
        observe(memory, second_sample)

        self.assertEqual(len(memory["recovery_cycles"]), 1)
        cycle = memory["recovery_cycles"][0]
        self.assertTrue(cycle["backfilled_from_overrun"])
        self.assertEqual(cycle["phase_end"], "target_reached")
        self.assertAlmostEqual(cycle["logical_duration_minutes"], 11.0)
        self.assertIsNone(cycle["start_error_c"])
        self.assertFalse(cycle["duration_sample_valid"])
        self.assertEqual(first["recovery"]["clean_target_cycles"], 1)
        self.assertEqual(
            first["recovery"]["models"]["cooling"]["usable_eta_samples"],
            0,
        )

    def test_equipment_stop_finalizes_overrun_model(self):
        memory = {}
        start = self.sample(1000)
        start.update(indoor_c=23, target_c=22, outdoor_c=30)
        observe(memory, start)

        target = self.sample(1600)
        target.update(indoor_c=22, target_c=22, outdoor_c=30)
        observe(memory, target)

        overrun = self.sample(1900)
        overrun.update(indoor_c=21.5, target_c=22, outdoor_c=30)
        observe(memory, overrun)

        stopped = self.sample(2200)
        stopped.update(
            action="idle",
            indoor_c=21.5,
            target_c=22,
            outdoor_c=30,
            condenser_w=0,
            blower_w=10,
        )
        result = observe(memory, stopped)

        self.assertEqual(len(memory["overrun_cycles"]), 1)
        record = memory["overrun_cycles"][0]
        self.assertAlmostEqual(record["duration_minutes"], 10.0)
        self.assertAlmostEqual(
            record["peak_thermostat_overshoot_c"],
            0.5,
        )
        model = result["recovery"]["overrun"]["models"]["cooling"]
        self.assertEqual(model["samples"], 1)
        self.assertAlmostEqual(model["duration_minutes"], 10.0)

    def test_brief_restart_resumes_post_target_overrun_and_total_call_clock(self):
        memory = {}
        start = self.sample(1000)
        start.update(indoor_c=23, target_c=22, outdoor_c=30)
        observe(memory, start)

        target = self.sample(1600)
        target.update(indoor_c=22, target_c=22, outdoor_c=30)
        observe(memory, target)
        self.assertIn("overrun_call", memory)

        restored = deepcopy(memory)
        restored["_restart_pending"] = True
        after_restart = self.sample(1660)
        after_restart.update(indoor_c=22, target_c=22, outdoor_c=30)
        result = observe(restored, after_restart)

        self.assertEqual(
            result["restart_resume"]["status"],
            "resumed_full_continuity",
        )
        self.assertTrue(
            result["restart_resume"]["resumed_overrun_call"]
        )
        self.assertTrue(result["recovery"]["overrun"]["active"])
        self.assertAlmostEqual(
            result["recovery"]["overrun"]["duration_minutes"],
            1.0,
        )
        self.assertAlmostEqual(
            result["recovery"]["equipment_call_minutes"],
            11.0,
        )
        self.assertEqual(len(restored["recovery_cycles"]), 1)

    def test_clean_target_cycle_replaces_legacy_population_for_eta(self):
        memory = {
            "recovery_cycles": [
                {
                    "ended_at": 800,
                    "action": "cooling",
                    "rate_c_per_hour": 1.0,
                    "outdoor_delta_c": 7,
                    "duration_minutes": 60,
                },
                {
                    "ended_at": 900,
                    "action": "cooling",
                    "rate_c_per_hour": 3.0,
                    "outdoor_delta_c": 7,
                    "duration_minutes": 20,
                    "phase_end": "target_reached",
                    "clean_target_cycle": True,
                },
            ]
        }
        sample = self.sample(1000)
        sample.update(indoor_c=23, target_c=22, outdoor_c=30)
        recovery = observe(memory, sample)["recovery"]

        self.assertEqual(
            recovery["models"]["cooling"]["population"],
            "clean_target_cycles",
        )
        self.assertEqual(recovery["models"]["cooling"]["samples"], 1)
        self.assertEqual(
            recovery["models"]["cooling"]["all_samples"],
            2,
        )
        self.assertAlmostEqual(recovery["rate_c_per_hour"], 3.0)
        self.assertAlmostEqual(recovery["eta_minutes"], 20.0)

    def test_equipment_stop_before_target_is_aborted_not_training(self):
        memory = {}
        start = self.sample(1000)
        start.update(indoor_c=23, target_c=22, outdoor_c=30)
        observe(memory, start)

        stopped = self.sample(1600)
        stopped.update(
            action="idle",
            indoor_c=22.5,
            target_c=22,
            outdoor_c=30,
            condenser_w=0,
            blower_w=10,
        )
        result = observe(memory, stopped)

        self.assertEqual(len(memory["recovery_cycles"]), 0)
        self.assertEqual(
            result["recovery"]["aborted_reasons"][
                "equipment_stopped_before_target"
            ],
            1,
        )

    def test_passive_thermal_learning_uses_homepod_precision_when_thermostat_is_flat(self):
        memory = {}
        for i in range(7):
            at = 1000 + i * 300
            sample = self.sample(at)
            sample.update(
                action="idle",
                indoor_c=22.0,
                precision_indoor_c=22.0 - 0.3 * (i / 6),
                target_c=22,
                outdoor_c=10,
                condenser_w=0,
                blower_w=10,
            )
            observe(memory, sample)

        active = self.sample(3100)
        active.update(
            action="cooling",
            indoor_c=22.0,
            precision_indoor_c=21.7,
            target_c=21,
            outdoor_c=10,
            condenser_w=2000,
            blower_w=200,
        )
        thermal = observe(memory, active)["thermal"]

        self.assertEqual(len(memory["thermal_samples"]), 1)
        self.assertEqual(
            thermal["temperature_signal_source"],
            "homepod_physical_room_median",
        )
        self.assertIsNotNone(thermal["coefficient_per_hour"])
        self.assertGreater(thermal["coefficient_per_hour"], 0)

    def test_hvac_temperature_signal_falls_back_to_thermostat(self):
        result = observe({}, self.sample(1000))
        self.assertEqual(result["temperature_signal"]["source"], "thermostat")
        self.assertEqual(result["temperature_signal"]["temperature_c"], 22)

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

    def test_thermal_rejections_explain_unusable_idle_windows(self):
        memory = {}
        for i in range(7):
            sample = self.sample(1000 + i * 300)
            sample.update(
                action="idle",
                indoor_c=22.0,
                target_c=22,
                outdoor_c=10,
                condenser_w=0,
                blower_w=10,
            )
            observe(memory, sample)

        active = self.sample(3100)
        active.update(
            action="cooling",
            indoor_c=22.0,
            target_c=21,
            outdoor_c=10,
            condenser_w=2000,
            blower_w=200,
        )
        result = observe(memory, active)

        thermal = result["thermal"]
        self.assertEqual(
            thermal["rejection_counts"]["insufficient_indoor_movement"],
            1,
        )
        self.assertEqual(thermal["rejected_windows"], 1)
        self.assertEqual(
            thermal["last_rejection"]["reason"],
            "insufficient_indoor_movement",
        )

    def test_one_completed_recovery_cycle_seeds_provisional_eta(self):
        memory = {}
        first = self.sample(1000)
        first.update(indoor_c=25, target_c=22, outdoor_c=30)
        observe(memory, first)

        progress = self.sample(1600)
        progress.update(indoor_c=24.7, target_c=22, outdoor_c=30)
        observe(memory, progress)

        target = self.sample(2200)
        target.update(
            action="cooling",
            indoor_c=22,
            target_c=22,
            outdoor_c=30,
            condenser_w=1500,
            blower_w=300,
        )
        observe(memory, target)
        self.assertEqual(len(memory["recovery_cycles"]), 1)

        stopped = self.sample(2500)
        stopped.update(
            action="idle",
            indoor_c=22,
            target_c=22,
            outdoor_c=30,
            condenser_w=0,
            blower_w=10,
        )
        observe(memory, stopped)

        next_call = self.sample(2800)
        next_call.update(indoor_c=25, target_c=22, outdoor_c=30)
        result = observe(memory, next_call)
        recovery = result["recovery"]

        self.assertEqual(recovery["source"], "history")
        self.assertEqual(recovery["status"], "provisional")
        self.assertEqual(recovery["confidence"], "low")
        self.assertIsNotNone(recovery["eta_minutes"])
        self.assertEqual(recovery["matched_cycles"], 1)

    def test_recovery_models_are_visible_while_idle(self):
        memory = {
            "recovery_cycles": [
                {
                    "ended_at": 1200,
                    "action": "cooling",
                    "rate_c_per_hour": 1.2,
                    "outdoor_delta_c": 5,
                    "duration_minutes": 30,
                },
                {
                    "ended_at": 1800,
                    "action": "cooling",
                    "rate_c_per_hour": 1.4,
                    "outdoor_delta_c": 6,
                    "duration_minutes": 35,
                },
                {
                    "ended_at": 2400,
                    "action": "cooling",
                    "rate_c_per_hour": 1.3,
                    "outdoor_delta_c": 5,
                    "duration_minutes": 32,
                },
            ]
        }
        idle = self.sample(3000)
        idle.update(
            action="idle",
            indoor_c=22,
            target_c=22,
            outdoor_c=30,
            condenser_w=0,
            blower_w=10,
        )
        result = observe(memory, idle)
        model = result["recovery"]["models"]["cooling"]

        self.assertEqual(model["samples"], 3)
        self.assertEqual(model["status"], "ready")
        self.assertEqual(model["confidence"], "high")
        self.assertAlmostEqual(model["rate_c_per_hour"], 1.3)

    def test_one_completed_thermal_window_remains_provisional_history(self):
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
            observe(memory, sample)

        active = self.sample(3100)
        active.update(
            action="cooling",
            indoor_c=21.7,
            target_c=21,
            outdoor_c=10,
            condenser_w=2000,
            blower_w=200,
        )
        result = observe(memory, active)
        thermal = result["thermal"]

        self.assertEqual(len(memory["thermal_samples"]), 1)
        self.assertEqual(thermal["source"], "history")
        self.assertEqual(thermal["status"], "provisional")
        self.assertEqual(thermal["confidence"], "low")
        self.assertIsNotNone(thermal["coefficient_per_hour"])
        self.assertIsNotNone(thermal["time_constant_hours"])
        self.assertEqual(thermal["samples_required"], 1)
        self.assertEqual(thermal["samples_required_for_ready"], 3)

    def test_two_thermal_samples_raise_confidence_without_hiding_estimate(self):
        memory = {
            "thermal_samples": [
                {
                    "ended_at": 1000,
                    "coefficient_per_hour": 0.05,
                    "observed_drift_c_per_hour": -0.3,
                    "duration_minutes": 60,
                    "mean_outdoor_delta_c": 6,
                },
                {
                    "ended_at": 2000,
                    "coefficient_per_hour": 0.07,
                    "observed_drift_c_per_hour": -0.35,
                    "duration_minutes": 60,
                    "mean_outdoor_delta_c": 5,
                },
            ]
        }
        sample = self.sample(3000)
        sample.update(
            action="cooling",
            indoor_c=22,
            target_c=21,
            outdoor_c=10,
            condenser_w=2000,
            blower_w=200,
        )
        result = observe(memory, sample)
        thermal = result["thermal"]

        self.assertEqual(thermal["status"], "provisional")
        self.assertEqual(thermal["confidence"], "medium")
        self.assertAlmostEqual(thermal["coefficient_per_hour"], 0.06)

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
