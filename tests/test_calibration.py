from __future__ import annotations

from pathlib import Path
import sys
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
sys.path.insert(0, str(MODULE_DIR))

from calibration import (  # noqa: E402
    apply_stored_energy_drop,
    build_profile,
    confidence_headroom_decision,
    projected_overnight_drop_kwh,
    quantile,
)


class CalibrationTests(unittest.TestCase):
    def test_learning_blocks_action_until_both_error_streams_have_samples(self) -> None:
        profile = build_profile(
            [{"error_kwh": -2.0, "error_ratio": -0.2}] * 2,
            [{"drop_rate_kw": 1.2}] * 5,
        )
        self.assertEqual(profile.status, "learning")
        self.assertFalse(profile.action_ready)
        self.assertEqual(profile.headroom_factor, 1.0)
        decision = confidence_headroom_decision(
            profile=profile,
            nominal_required_headroom_kwh=25.0,
            conservative_available_headroom_kwh=15.0,
            stored_above_reserve_kwh=20.0,
        )
        self.assertFalse(decision.action_ready)
        self.assertEqual(decision.recommended_additional_discharge_kwh, 0.0)
        self.assertEqual(decision.confidence_required_headroom_kwh, 25.0)
        self.assertEqual(decision.confidence_shortfall_kwh, 10.0)

    def test_small_sample_uses_export_defense_solar_tail_and_overnight_lower_bound(self) -> None:
        profile = build_profile(
            [
                {"error_kwh": -1.0, "error_ratio": -0.05},
                {"error_kwh": -3.0, "error_ratio": -0.30},
                {"error_kwh": 1.0, "error_ratio": 0.10},
            ],
            [
                {"drop_rate_kw": 1.5},
                {"drop_rate_kw": 1.0},
                {"drop_rate_kw": 1.3},
            ],
        )
        self.assertTrue(profile.action_ready)
        self.assertEqual(profile.status, "calibrating")
        self.assertAlmostEqual(profile.daylight_lower_error_ratio, -0.30)
        self.assertAlmostEqual(profile.headroom_factor, 1.10)
        self.assertAlmostEqual(profile.overnight_lower_drop_kw, 1.0)
        self.assertAlmostEqual(profile.overnight_upper_drop_kw, 1.5)

    def test_positive_daylight_errors_increase_headroom_request_with_a_cap(self) -> None:
        profile = build_profile(
            [
                {"error_kwh": 1.0, "error_ratio": 0.10},
                {"error_kwh": 2.0, "error_ratio": 0.20},
                {"error_kwh": 3.0, "error_ratio": 0.30},
            ],
            [
                {"drop_rate_kw": 1.0},
                {"drop_rate_kw": 1.1},
                {"drop_rate_kw": 1.2},
            ],
        )
        self.assertEqual(profile.daylight_lower_error_ratio, 0.0)
        self.assertEqual(profile.headroom_factor, 1.25)

    def test_ten_samples_use_empirical_tails_not_single_outlier(self) -> None:
        ratios = [-0.9, -0.2, -0.18, -0.15, -0.1, -0.08, -0.05, 0.0, 0.02, 0.05]
        daylight = [
            {"error_kwh": ratio * 10.0, "error_ratio": ratio}
            for ratio in ratios
        ]
        rates = [1.0 + i * 0.1 for i in range(10)]
        overnight = [{"drop_rate_kw": rate} for rate in rates]
        profile = build_profile(daylight, overnight)
        self.assertEqual(profile.status, "calibrated")
        self.assertAlmostEqual(
            profile.daylight_lower_error_ratio,
            min(quantile(ratios, 0.10), 0.0),
        )
        self.assertAlmostEqual(profile.overnight_upper_drop_kw, quantile(rates, 0.90))
        expected_factor = min(1.0 + max(quantile(ratios, 0.90), 0.0), 1.25)
        self.assertAlmostEqual(profile.headroom_factor, expected_factor)

    def test_overnight_drop_is_bounded_by_reserve(self) -> None:
        drop = projected_overnight_drop_kwh(
            drop_kw=3.0,
            night_hours=12.0,
            sunset_soc_pct=30.0,
            capacity_kwh=49.152,
            reserve_pct=10.0,
        )
        self.assertAlmostEqual(drop, 49.152 * 0.20)

    def test_bank_drop_preserves_reserve_and_weighted_energy(self) -> None:
        capacities = (18.432, 12.288, 18.432)
        ending = apply_stored_energy_drop(
            bank_socs_pct=(60.0, 50.0, 40.0),
            bank_capacities_kwh=capacities,
            drop_kwh=5.0,
            reserve_pct=10.0,
        )
        before = sum(
            capacity * soc / 100.0
            for capacity, soc in zip(capacities, (60.0, 50.0, 40.0))
        )
        after = sum(
            capacity * soc / 100.0
            for capacity, soc in zip(capacities, ending)
        )
        self.assertAlmostEqual(before - after, 5.0, places=6)
        self.assertTrue(all(soc >= 10.0 for soc in ending))

    def test_confidence_decision_never_discounts_overestimated_solar(self) -> None:
        profile = build_profile(
            [
                {"error_kwh": -2.0, "error_ratio": -0.20},
                {"error_kwh": -1.0, "error_ratio": -0.10},
                {"error_kwh": -3.0, "error_ratio": -0.30},
            ],
            [
                {"drop_rate_kw": 1.0},
                {"drop_rate_kw": 1.2},
                {"drop_rate_kw": 1.5},
            ],
        )
        # Historical solar overestimates do not discount export headroom need.
        decision = confidence_headroom_decision(
            profile=profile,
            nominal_required_headroom_kwh=25.0,
            conservative_available_headroom_kwh=18.0,
            stored_above_reserve_kwh=20.0,
        )
        self.assertAlmostEqual(decision.confidence_required_headroom_kwh, 25.0)
        self.assertEqual(decision.confidence_shortfall_kwh, 7.0)
        self.assertEqual(decision.recommended_additional_discharge_kwh, 7.0)

    def test_confidence_decision_recommends_only_robust_shortfall(self) -> None:
        profile = build_profile(
            [
                {"error_kwh": -1.0, "error_ratio": -0.10},
                {"error_kwh": -2.0, "error_ratio": -0.20},
                {"error_kwh": -1.5, "error_ratio": -0.15},
            ],
            [
                {"drop_rate_kw": 0.5},
                {"drop_rate_kw": 0.8},
                {"drop_rate_kw": 0.6},
            ],
        )
        decision = confidence_headroom_decision(
            profile=profile,
            nominal_required_headroom_kwh=30.0,
            conservative_available_headroom_kwh=20.0,
            stored_above_reserve_kwh=15.0,
        )
        # Solar overestimation does not discount a 30 kWh export-defense need.
        self.assertAlmostEqual(decision.confidence_required_headroom_kwh, 30.0)
        self.assertAlmostEqual(decision.recommended_additional_discharge_kwh, 10.0)


if __name__ == "__main__":
    unittest.main()
