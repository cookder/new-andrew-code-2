"""Persistence rules: the generated column, flag lifecycle, and the review gate."""

from __future__ import annotations

import pytest

from backend.app import store
from backend.app.constants import (
    FLAG_SMASH_MISMATCH,
    FLAG_SUSPECTED_CLUB_CHANGE,
    FLAG_ZERO_SPIN,
)

from .conftest import make_shot


class TestGeneratedFaceToPath:
    def test_derived_from_face_angle_and_club_path(self, conn, session):
        shot = make_shot(conn, session["id"], face_angle=1.2, club_path=-0.6)
        assert shot["face_to_path"] == pytest.approx(1.8)

    def test_tracks_edits_to_its_inputs(self, conn, session):
        shot = make_shot(conn, session["id"])
        updated = store.update_shot(conn, shot["id"], face_angle=3.0, club_path=1.0)
        assert updated["face_to_path"] == pytest.approx(2.0)

    def test_cannot_be_written_directly(self, conn, session):
        # Section 2: "Face to path is derived, never OCR'd." Making the column
        # GENERATED means the pipeline cannot persist an OCR'd value by mistake.
        shot = make_shot(conn, session["id"])
        with pytest.raises(Exception):
            conn.execute(
                "UPDATE shots SET face_to_path = 99 WHERE id = ?", (shot["id"],)
            )

    def test_null_when_an_input_is_missing(self, conn, session):
        shot = make_shot(conn, session["id"], club_path=None)
        assert shot["face_to_path"] is None


class TestShotWrites:
    def test_clean_shot_has_no_flags(self, conn, session):
        assert make_shot(conn, session["id"])["flags"] == []

    def test_bad_smash_is_flagged_on_write(self, conn, session):
        shot = make_shot(conn, session["id"], smash_factor=1.55)
        assert FLAG_SMASH_MISMATCH in shot["flags"]

    def test_editing_the_value_clears_the_flag(self, conn, session):
        shot = make_shot(conn, session["id"], smash_factor=1.55)
        fixed = store.update_shot(conn, shot["id"], smash_factor=1.41)
        assert FLAG_SMASH_MISMATCH not in fixed["flags"]

    def test_zero_spin_is_stored_as_missing(self, conn, session):
        shot = make_shot(conn, session["id"], spin_rate=0, spin_axis=0)
        assert shot["spin_rate"] is None
        assert shot["spin_axis"] is None
        assert FLAG_ZERO_SPIN in shot["flags"]

    def test_shot_index_auto_increments(self, conn, session):
        a = make_shot(conn, session["id"])
        b = make_shot(conn, session["id"])
        assert (a["shot_index"], b["shot_index"]) == (1, 2)

    def test_manual_entry_records_its_club_source(self, conn, session):
        shot = store.create_shot(conn, session["id"], club="9 iron", carry=130)
        assert shot["club_source"] == "manual"

    def test_inline_club_edit_becomes_manual(self, conn, session):
        shot = make_shot(conn, session["id"], club_source="inherited")
        updated = store.update_shot(conn, shot["id"], club="8 iron")
        assert updated["club_source"] == "manual"

    def test_tags_are_normalized_and_deduped(self, conn, session):
        shot = make_shot(conn, session["id"], tags=[" Thin", "thin", "PULL"])
        assert shot["tags"] == ["pull", "thin"]


class TestReviewGate:
    def test_shots_start_unreviewed(self, conn, session):
        # Section 6: "Nothing enters the confirmed dataset unreviewed."
        assert make_shot(conn, session["id"])["confidence"] == "needs_review"

    def test_confirming_promotes_every_shot(self, conn, session):
        make_shot(conn, session["id"])
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        shots = store.list_shots(conn, session["id"])
        assert all(s["confidence"] == "high" for s in shots)
        assert all(s["reviewed_at"] for s in shots)
        assert store.get_session(conn, session["id"])["status"] == "confirmed"

    def test_unconfirming_pulls_them_back_out(self, conn, session):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        store.unconfirm_session(conn, session["id"])
        shots = store.list_shots(conn, session["id"])
        assert all(s["confidence"] == "needs_review" for s in shots)
        assert store.get_session(conn, session["id"])["status"] == "extracted"

    def test_confirming_does_not_erase_flags(self, conn, session):
        # A zero-spin reading is still missing after review; confirming records
        # that a human looked, not that the data became perfect.
        make_shot(conn, session["id"], spin_rate=0, spin_axis=0)
        store.confirm_session(conn, session["id"])
        assert FLAG_ZERO_SPIN in store.list_shots(conn, session["id"])[0]["flags"]


class TestBulkClubReassignment:
    def test_reassigns_a_contiguous_range(self, conn, session):
        for _ in range(5):
            make_shot(conn, session["id"], club="7 iron")
        updated = store.bulk_set_club(conn, session["id"], "8 iron", from_index=2, to_index=4)
        assert updated == 3
        clubs = [s["club"] for s in store.list_shots(conn, session["id"])]
        assert clubs == ["7 iron", "8 iron", "8 iron", "8 iron", "7 iron"]

    def test_marks_the_range_as_manual(self, conn, session):
        make_shot(conn, session["id"], club="7 iron", club_source="inherited")
        store.bulk_set_club(conn, session["id"], "8 iron", from_index=1, to_index=1)
        assert store.list_shots(conn, session["id"])[0]["club_source"] == "manual"

    def test_reversed_range_is_accepted(self, conn, session):
        for _ in range(3):
            make_shot(conn, session["id"], club="7 iron")
        assert store.bulk_set_club(conn, session["id"], "8 iron", from_index=3, to_index=1) == 3

    def test_clears_the_flag_it_was_prompted_by(self, conn, session):
        # The bulk action is the repair for an unannounced switch, so the
        # suspicion it resolves should not linger on the rows.
        make_shot(conn, session["id"], club="7 iron", club_source="stated", ball_speed=118.4)
        make_shot(conn, session["id"], club="7 iron", club_source="inherited", ball_speed=117.0)
        make_shot(
            conn,
            session["id"],
            club="7 iron",
            club_source="inherited",
            ball_speed=152.0,
            club_speed=104.0,
            smash_factor=1.46,
        )
        store.rerun_club_change_guard(conn, session["id"])
        shots = store.list_shots(conn, session["id"])
        assert FLAG_SUSPECTED_CLUB_CHANGE in shots[2]["flags"]

        store.bulk_set_club(conn, session["id"], "driver", from_index=3, to_index=3)
        after = store.list_shots(conn, session["id"])[2]
        assert after["club"] == "driver"
        assert FLAG_SUSPECTED_CLUB_CHANGE not in after["flags"]


class TestCascades:
    def test_deleting_a_session_removes_its_shots(self, conn, session):
        make_shot(conn, session["id"])
        store.delete_session(conn, session["id"])
        assert conn.execute("SELECT COUNT(*) AS n FROM shots").fetchone()["n"] == 0

    def test_artifacts_are_upserted_per_kind(self, conn, session):
        shot = make_shot(conn, session["id"])
        store.add_artifact(conn, shot["id"], "clip", "/tmp/a.mp4")
        store.add_artifact(conn, shot["id"], "clip", "/tmp/b.mp4")
        artifacts = store.get_artifacts(conn, shot["id"])
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "/tmp/b.mp4"
