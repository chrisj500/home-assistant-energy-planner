from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"))
from hvac import celsius, hourly_forecast, observe, room_summary


class HVACTests(unittest.TestCase):
    def sample(self, at=1000, **kwargs):
        return dict(at=at, mode="cool", action="cooling", target_c=22,
                    indoor_c=22, outdoor_c=30, outdoor_humidity=60,
                    humidity=60, condenser_w=2000, blower_w=200, **kwargs)

    def test_temperature_conversion_and_invalid_values(self):
        self.assertAlmostEqual(celsius(72, "°F"), 22.222222, places=5)
        for value, unit in [("nan", "°C"), (71, "K"), (500, "°C")]:
            self.assertIsNone(celsius(value, unit))

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
        self.assertEqual(observe({}, sample)["status"], "unavailable")

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
