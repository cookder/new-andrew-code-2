"""Spec section 7 -- the analysis layer."""

from __future__ import annotations

import pytest

from backend.app import analysis, store
from backend.app.constants import TREND_MIN_SESSIONS, TREND_MIN_SHOTS

from .conftest import make_shot


class TestSummarize:
    def test_median_not_mean(self):
        # A single 400-yard mishit reading must not drag the centre of the
        # distribution the way a mean would.
        summary = analysis.summarize([150, 152, 154, 156, 400])
        assert summary["median"] == 154
        assert summary["n"] == 5

    def test_interquartile_range(self):
        summary = analysis.summarize([10, 20, 30, 40, 50])
        assert summary["q1"] == 20
        assert summary["q3"] == 40
        assert summary["iqr"] == 20

    def test_missing_values_are_excluded_from_n(self):
        # n is values present, not shots in the group -- otherwise a spin median
        # would claim a sample it does not have.
        summary = analysis.summarize([150, None, 160, None])
        assert summary["n"] == 2
        assert summary["median"] == 155

    def test_single_value(self):
        summary = analysis.summarize([42])
        assert (summary["n"], summary["median"], summary["iqr"]) == (1, 42, 0)

    def test_empty(self):
        summary = analysis.summarize([None, None])
        assert summary["n"] == 0
        assert summary["median"] is None
        assert summary["iqr"] is None


class TestConfirmedOnly:
    def test_unconfirmed_shots_are_invisible_to_analysis(self, conn, session):
        make_shot(conn, session["id"])
        assert analysis.per_club(conn)["clubs"] == []

    def test_confirming_makes_them_visible(self, conn, session):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        clubs = analysis.per_club(conn)["clubs"]
        assert len(clubs) == 1
        assert clubs[0]["shot_count"] == 1


class TestPerClub:
    def test_groups_and_counts(self, conn, session):
        for _ in range(3):
            make_shot(conn, session["id"], club="7 iron")
        make_shot(conn, session["id"], club="driver", ball_speed=150, club_speed=103, smash_factor=1.46)
        store.confirm_session(conn, session["id"])

        by_club = {c["club"]: c for c in analysis.per_club(conn)["clubs"]}
        assert by_club["7 iron"]["shot_count"] == 3
        assert by_club["driver"]["shot_count"] == 1

    def test_reports_units_so_nothing_is_implied(self, conn):
        units = analysis.per_club(conn)["units"]
        assert units["apex"] == "feet"
        assert units["carry"] == "yards"
        assert units["smash_factor"] == "ratio"

    def test_every_metric_gets_a_summary(self, conn, session):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        metrics = analysis.per_club(conn)["clubs"][0]["metrics"]
        for key in ("carry", "ball_speed", "smash_factor", "apex", "face_to_path"):
            assert "median" in metrics[key] and "iqr" in metrics[key]


class TestStrikeVsOutcome:
    def _seed_strikes(self, conn, session):
        for _ in range(4):
            make_shot(
                conn, session["id"], strike_location="center",
                carry=165, ball_speed=118.4, club_speed=84.0, smash_factor=1.41,
            )
        for _ in range(3):
            make_shot(
                conn, session["id"], strike_location="heel",
                carry=148, ball_speed=110.0, club_speed=84.0, smash_factor=1.31,
            )
        make_shot(conn, session["id"], strike_location=None)
        store.confirm_session(conn, session["id"])

    def test_groups_by_strike_location(self, conn, session):
        self._seed_strikes(conn, session)
        club = analysis.strike_vs_outcome(conn)["clubs"][0]
        by_loc = {loc["strike_location"]: loc for loc in club["locations"]}
        assert by_loc["center"]["shot_count"] == 4
        assert by_loc["heel"]["shot_count"] == 3

    def test_compares_smash_and_carry(self, conn, session):
        self._seed_strikes(conn, session)
        club = analysis.strike_vs_outcome(conn)["clubs"][0]
        by_loc = {loc["strike_location"]: loc for loc in club["locations"]}
        assert by_loc["center"]["smash_factor"]["median"] > by_loc["heel"]["smash_factor"]["median"]
        assert by_loc["center"]["carry"]["median"] > by_loc["heel"]["carry"]["median"]

    def test_reports_dispersion(self, conn, session):
        self._seed_strikes(conn, session)
        club = analysis.strike_vs_outcome(conn)["clubs"][0]
        dispersion = club["locations"][0]["dispersion"]
        assert set(dispersion) == {"carry_iqr", "spin_axis_iqr", "carry_range"}

    def test_unattributed_shots_are_kept_and_counted_separately(self, conn, session):
        self._seed_strikes(conn, session)
        club = analysis.strike_vs_outcome(conn)["clubs"][0]
        assert club["shot_count"] == 8
        assert club["attributed_count"] == 7
        assert club["locations"][-1]["strike_location"] is None

    def test_can_filter_to_one_club(self, conn, session):
        self._seed_strikes(conn, session)
        make_shot(conn, session["id"], club="driver")
        store.confirm_session(conn, session["id"])
        result = analysis.strike_vs_outcome(conn, club="driver")
        assert [c["club"] for c in result["clubs"]] == ["driver"]


class TestTrendGate:
    def _seed(self, conn, sessions: int, per_session: int):
        for i in range(sessions):
            s = store.create_session(conn, recorded_at=f"2026-0{i + 1}-01T17:00:00")
            for _ in range(per_session):
                make_shot(conn, s["id"])
            store.confirm_session(conn, s["id"])

    def test_below_shot_threshold_is_gated(self, conn):
        self._seed(conn, sessions=3, per_session=5)
        club = analysis.session_trend(conn)["clubs"][0]
        assert club["trend_eligible"] is False
        assert club["trend_gate"]["shots_short_by"] == TREND_MIN_SHOTS - 15

    def test_below_session_threshold_is_gated(self, conn):
        self._seed(conn, sessions=2, per_session=20)
        club = analysis.session_trend(conn)["clubs"][0]
        assert club["trend_eligible"] is False
        assert club["trend_gate"]["sessions_short_by"] == TREND_MIN_SESSIONS - 2

    def test_clearing_both_thresholds_is_eligible(self, conn):
        self._seed(conn, sessions=3, per_session=11)
        club = analysis.session_trend(conn)["clubs"][0]
        assert club["shot_count"] == 33
        assert club["session_count"] == 3
        assert club["trend_eligible"] is True

    def test_points_are_returned_even_when_gated(self, conn):
        # The gate governs whether a *line* is drawn, not whether the data is
        # returned -- the UI still shows the points and the sample sizes.
        self._seed(conn, sessions=2, per_session=4)
        club = analysis.session_trend(conn)["clubs"][0]
        assert club["trend_eligible"] is False
        assert len(club["points"]) == 2

    def test_per_session_shot_counts_are_visible(self, conn):
        self._seed(conn, sessions=3, per_session=6)
        club = analysis.session_trend(conn)["clubs"][0]
        assert [p["shot_count"] for p in club["points"]] == [6, 6, 6]

    def test_points_are_in_chronological_order(self, conn):
        self._seed(conn, sessions=3, per_session=2)
        dates = [p["session_date"] for p in analysis.session_trend(conn)["clubs"][0]["points"]]
        assert dates == sorted(dates)

    def test_metric_is_selectable(self, conn, session):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        result = analysis.session_trend(conn, metric="ball_speed")
        assert result["unit"] == "mph"
        assert result["label"] == "Ball speed"

    def test_unknown_metric_rejected(self, conn):
        with pytest.raises(ValueError):
            analysis.session_trend(conn, metric="vibes")


class TestDispersion:
    def test_returns_face_angle_against_club_path(self, conn, session):
        make_shot(conn, session["id"], face_angle=2.0, club_path=-1.0)
        store.confirm_session(conn, session["id"])
        result = analysis.dispersion(conn)
        point = result["points"][0]
        assert (point["face_angle"], point["club_path"]) == (2.0, -1.0)
        assert point["face_to_path"] == pytest.approx(3.0)
        assert point["strike_location"] == "center"

    def test_skips_shots_without_both_angles(self, conn, session):
        make_shot(conn, session["id"], face_angle=None)
        store.confirm_session(conn, session["id"])
        assert analysis.dispersion(conn)["point_count"] == 0


class TestShotBrowser:
    def test_defaults_to_confirmed_only(self, conn, session):
        make_shot(conn, session["id"])
        assert analysis.shot_browser(conn)["total"] == 0
        store.confirm_session(conn, session["id"])
        assert analysis.shot_browser(conn)["total"] == 1

    def test_filters_by_club_and_strike(self, conn, session):
        make_shot(conn, session["id"], club="7 iron", strike_location="heel")
        make_shot(conn, session["id"], club="driver", strike_location="center")
        store.confirm_session(conn, session["id"])
        assert analysis.shot_browser(conn, club="7 iron")["total"] == 1
        assert analysis.shot_browser(conn, strike_location="center")["total"] == 1

    def test_filters_by_numeric_range(self, conn, session):
        make_shot(conn, session["id"], carry=120)
        make_shot(conn, session["id"], carry=180)
        store.confirm_session(conn, session["id"])
        assert analysis.shot_browser(conn, carry_min=150)["total"] == 1
        assert analysis.shot_browser(conn, carry_max=150)["total"] == 1

    def test_filters_by_tag(self, conn, session):
        make_shot(conn, session["id"], tags=["thin"])
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        assert analysis.shot_browser(conn, tag="thin")["total"] == 1

    def test_filters_by_free_text(self, conn, session):
        make_shot(conn, session["id"], ball_flight="started right, hooked back")
        make_shot(conn, session["id"], ball_flight="dead straight")
        store.confirm_session(conn, session["id"])
        assert analysis.shot_browser(conn, text="hooked")["total"] == 1

    def test_can_show_flagged_rows_before_review(self, conn, session):
        make_shot(conn, session["id"], smash_factor=1.55)
        result = analysis.shot_browser(conn, confirmed_only=False, flagged=True)
        assert result["total"] == 1

    def test_carries_artifacts_for_click_through(self, conn, session):
        shot = make_shot(conn, session["id"])
        store.add_artifact(conn, shot["id"], "clip", "/tmp/clip.mp4")
        store.confirm_session(conn, session["id"])
        row = analysis.shot_browser(conn)["shots"][0]
        assert row["artifacts"]["clip"] == "/tmp/clip.mp4"

    def test_pagination(self, conn, session):
        for _ in range(5):
            make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        page = analysis.shot_browser(conn, limit=2, offset=2)
        assert page["total"] == 5
        assert len(page["shots"]) == 2
