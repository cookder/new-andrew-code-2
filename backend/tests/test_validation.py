"""Spec section 3 -- the two arithmetic identities and the range checks."""

from __future__ import annotations

import pytest

from backend.app.constants import (
    FLAG_FACE_TO_PATH_MISMATCH,
    FLAG_MISSING_METRIC,
    FLAG_OUT_OF_RANGE,
    FLAG_SMASH_MISMATCH,
    FLAG_SUSPECTED_CLUB_CHANGE,
    FLAG_ZERO_SPIN,
)
from backend.app.validation import (
    check_face_to_path,
    check_ranges,
    check_smash,
    derive_face_to_path,
    flag_unannounced_club_changes,
    normalize_metrics,
    suspected_club_change,
    validate_shot,
)


class TestSmashIdentity:
    def test_consistent_values_pass(self):
        assert check_smash(118.4, 84.0, 1.41) is None

    def test_just_inside_tolerance_passes(self):
        # 118.4 / 84.0 = 1.4095; +0.019 is inside the +/-0.02 band
        assert check_smash(118.4, 84.0, 1.4285) is None

    def test_just_outside_tolerance_flags(self):
        assert check_smash(118.4, 84.0, 1.44) == FLAG_SMASH_MISMATCH

    def test_missing_input_cannot_be_checked(self):
        assert check_smash(None, 84.0, 1.41) is None
        assert check_smash(118.4, None, 1.41) is None
        assert check_smash(118.4, 84.0, None) is None

    def test_zero_club_speed_does_not_divide(self):
        assert check_smash(118.4, 0, 1.41) is None


class TestFaceToPathIdentity:
    def test_matching_observation_passes(self):
        assert check_face_to_path(1.2, -0.6, 1.8) is None

    def test_within_tolerance_passes(self):
        assert check_face_to_path(1.2, -0.6, 1.95) is None

    def test_outside_tolerance_flags(self):
        assert check_face_to_path(1.2, -0.6, 2.4) == FLAG_FACE_TO_PATH_MISMATCH

    def test_no_observation_means_nothing_to_verify(self):
        # The stored value is derived, so with no OCR reading the identity is
        # true by construction and there is nothing to check.
        assert check_face_to_path(1.2, -0.6, None) is None

    def test_derive_is_the_single_definition(self):
        assert derive_face_to_path(1.2, -0.6) == pytest.approx(1.8)
        assert derive_face_to_path(None, -0.6) is None


class TestZeroSpin:
    def test_zero_spin_becomes_missing_not_measured(self):
        cleaned, flags = normalize_metrics({"spin_rate": 0, "spin_axis": 0, "carry": 160})
        assert cleaned["spin_rate"] is None
        assert cleaned["spin_axis"] is None
        assert FLAG_ZERO_SPIN in flags

    def test_zero_carry_is_a_real_measurement(self):
        cleaned, flags = normalize_metrics({"carry": 0})
        assert cleaned["carry"] == 0.0
        assert FLAG_ZERO_SPIN not in flags

    def test_missing_spin_is_not_counted_as_a_hole(self):
        # Spin absence is already reported by FLAG_ZERO_SPIN; it must not also
        # trip the missing-metric flag or every second shot would look broken.
        cleaned, _ = normalize_metrics({"spin_rate": 0, "spin_axis": 0})
        flags = validate_shot(cleaned, club="7 iron")
        missing = [f for f in flags if f.startswith(FLAG_MISSING_METRIC)]
        assert missing
        assert "spin_rate" not in missing[0]
        assert "spin_axis" not in missing[0]


class TestRangeChecks:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("ball_speed", 240),
            ("club_speed", 150),
            ("smash_factor", 1.7),
            ("smash_factor", 0.5),
            ("spin_rate", 13000),
            ("launch_angle", 70),
            ("launch_angle", -9),
        ],
    )
    def test_implausible_values_flag(self, field, value):
        flags = check_ranges({field: value})
        assert f"{FLAG_OUT_OF_RANGE}:{field}" in flags

    def test_plausible_values_do_not_flag(self):
        assert check_ranges({"ball_speed": 150, "smash_factor": 1.45}) == []

    def test_range_failure_flags_rather_than_rejects(self):
        # Section 3 is explicit: "flag, do not reject".
        flags = validate_shot({"ball_speed": 999}, club="7 iron", require_all_metrics=False)
        assert any(f.startswith(FLAG_OUT_OF_RANGE) for f in flags)


class TestUnannouncedClubChange:
    def test_speed_within_band_is_not_suspicious(self):
        assert suspected_club_change(119.0, [117.0, 118.0, 119.0]) is False

    def test_large_jump_is_suspicious(self):
        assert suspected_club_change(140.0, [117.0, 118.0, 119.0]) is True

    def test_large_drop_is_also_suspicious(self):
        assert suspected_club_change(95.0, [117.0, 118.0, 119.0]) is True

    def test_boundary_is_exclusive(self):
        # Exactly 12 mph off the median is not "more than 12".
        assert suspected_club_change(130.0, [118.0]) is False
        assert suspected_club_change(130.1, [118.0]) is True

    def test_only_the_trailing_three_shots_count(self):
        # The 60 is old news and must not drag the median down.
        assert suspected_club_change(120.0, [60.0, 117.0, 118.0, 119.0]) is False

    def test_no_history_means_no_judgement(self):
        assert suspected_club_change(150.0, []) is False

    def test_flags_across_a_session_reset_on_announcement(self):
        shots = [
            {"club_source": "stated", "ball_speed": 118.0},
            {"club_source": "inherited", "ball_speed": 117.0},
            {"club_source": "inherited", "ball_speed": 119.0},
            # Golfer picked up the driver without saying so.
            {"club_source": "inherited", "ball_speed": 152.0},
        ]
        flags = flag_unannounced_club_changes(shots)
        assert flags[:3] == [[], [], []]
        assert FLAG_SUSPECTED_CLUB_CHANGE in flags[3]

    def test_announcement_clears_the_trailing_window(self):
        # A stated club is ground truth, so the speeds before it belong to a
        # different club and must not be compared against.
        shots = [
            {"club_source": "stated", "ball_speed": 118.0},
            {"club_source": "inherited", "ball_speed": 119.0},
            {"club_source": "stated", "ball_speed": 152.0},
            {"club_source": "inherited", "ball_speed": 154.0},
        ]
        flags = flag_unannounced_club_changes(shots)
        assert all(f == [] for f in flags)
