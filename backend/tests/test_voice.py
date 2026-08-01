"""Spec stage 6 -- club persistence and the unannounced-change guard.

``resolve_clubs`` is the whole rule as a pure function over already-extracted
attributions, so it can be tested without audio, a transcript, or the network.
"""

from __future__ import annotations

from backend.app.constants import FLAG_NO_CLUB, FLAG_SUSPECTED_CLUB_CHANGE
from backend.app.pipeline.transcription import window
from backend.app.pipeline.voice import resolve_clubs


def attribution(club: str | None = None, **extra):
    return {
        "club_stated": club,
        "strike_location": None,
        "ball_flight": None,
        "self_assessment": None,
        "tags": [],
        **extra,
    }


class TestClubPersistence:
    def test_stated_club_is_used(self):
        resolved = resolve_clubs([attribution("7 iron")], [118.0])
        assert resolved[0]["club"] == "7 iron"
        assert resolved[0]["club_source"] == "stated"

    def test_silence_inherits_the_previous_club(self):
        resolved = resolve_clubs(
            [attribution("7 iron"), attribution(None), attribution(None)],
            [118.0, 117.0, 119.0],
        )
        assert [r["club"] for r in resolved] == ["7 iron", "7 iron", "7 iron"]
        assert [r["club_source"] for r in resolved] == ["stated", "inherited", "inherited"]

    def test_a_new_announcement_takes_over(self):
        resolved = resolve_clubs(
            [attribution("7 iron"), attribution(None), attribution("driver"), attribution(None)],
            [118.0, 117.0, 150.0, 152.0],
        )
        assert [r["club"] for r in resolved] == ["7 iron", "7 iron", "driver", "driver"]

    def test_nothing_to_inherit_is_flagged(self):
        resolved = resolve_clubs([attribution(None)], [118.0])
        assert resolved[0]["club"] is None
        assert FLAG_NO_CLUB in resolved[0]["flags"]


class TestUnannouncedChangeGuard:
    def test_steady_speeds_are_not_flagged(self):
        resolved = resolve_clubs(
            [attribution("7 iron"), attribution(None), attribution(None), attribution(None)],
            [118.0, 117.0, 119.0, 118.5],
        )
        assert all(r["flags"] == [] for r in resolved)

    def test_a_silent_switch_is_flagged(self):
        resolved = resolve_clubs(
            [attribution("7 iron"), attribution(None), attribution(None), attribution(None)],
            [118.0, 117.0, 119.0, 152.0],
        )
        assert FLAG_SUSPECTED_CLUB_CHANGE in resolved[3]["flags"]

    def test_the_flag_does_not_change_the_club(self):
        # Spec: "Do not auto-correct." The row is surfaced for a human, and the
        # club stays as inherited until someone says otherwise.
        resolved = resolve_clubs(
            [attribution("7 iron"), attribution(None), attribution(None), attribution(None)],
            [118.0, 117.0, 119.0, 152.0],
        )
        assert resolved[3]["club"] == "7 iron"
        assert resolved[3]["club_source"] == "inherited"

    def test_an_announced_switch_is_never_flagged(self):
        resolved = resolve_clubs(
            [attribution("7 iron"), attribution("driver"), attribution(None)],
            [118.0, 152.0, 154.0],
        )
        assert all(r["flags"] == [] for r in resolved)

    def test_first_shot_of_a_block_has_no_history_to_compare(self):
        resolved = resolve_clubs([attribution("driver"), attribution(None)], [152.0, 150.0])
        assert all(r["flags"] == [] for r in resolved)

    def test_missing_ball_speed_cannot_trip_the_guard(self):
        resolved = resolve_clubs(
            [attribution("7 iron"), attribution(None)], [118.0, None]
        )
        assert resolved[1]["flags"] == []


class TestVoiceFields:
    def test_extracted_fields_are_carried_through(self):
        resolved = resolve_clubs(
            [
                attribution(
                    "7 iron",
                    strike_location="heel",
                    ball_flight="cut across it",
                    self_assessment="felt it in the hosel",
                    tags=["heel", "fade"],
                )
            ],
            [110.0],
        )
        entry = resolved[0]
        assert entry["strike_location"] == "heel"
        assert entry["ball_flight"] == "cut across it"
        assert entry["tags"] == ["heel", "fade"]


class TestTranscriptWindow:
    def test_selects_words_inside_the_window(self):
        words = [
            {"word": "seven", "start": 10.0, "end": 10.4},
            {"word": "iron", "start": 10.5, "end": 10.9},
            {"word": "heel", "start": 32.0, "end": 32.4},
            {"word": "later", "start": 99.0, "end": 99.4},
        ]
        assert window(words, 5.0, 40.0) == "seven iron heel"

    def test_excludes_words_outside_the_window(self):
        words = [{"word": "early", "start": 1.0, "end": 1.2}]
        assert window(words, 5.0, 40.0) == ""
