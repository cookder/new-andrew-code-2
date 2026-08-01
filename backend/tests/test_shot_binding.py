"""Spec section 4, stage 3 -- binding panel changes to swings.

Everything here follows from the timing behavior in section 1: the panel shows
the PREVIOUS shot and refreshes 2-3s after impact, so a panel change binds
*backwards* to the swing that caused it.
"""

from __future__ import annotations

from backend.app.constants import FLAG_UNMATCHED_IMPACT, FLAG_UNMATCHED_PANEL_CHANGE
from backend.app.pipeline.shot_detection import bind_shots


def test_each_change_binds_to_the_preceding_impact():
    result = bind_shots(panel_changes=[12.5, 45.4], impacts=[10.0, 43.0])
    assert [(s.impact_ts, s.panel_change_ts) for s in result.shots] == [
        (10.0, 12.5),
        (43.0, 45.4),
    ]
    assert result.anomalies == []


def test_shot_indexes_are_sequential_in_time_order():
    result = bind_shots(panel_changes=[45.4, 12.5, 80.1], impacts=[43.0, 10.0, 78.0])
    assert [s.shot_index for s in result.shots] == [1, 2, 3]
    assert [s.impact_ts for s in result.shots] == [10.0, 43.0, 78.0]


def test_a_later_impact_never_binds_to_an_earlier_change():
    # The panel cannot show a shot that has not happened yet.
    result = bind_shots(panel_changes=[10.0], impacts=[12.0])
    assert result.shots == []
    assert (FLAG_UNMATCHED_PANEL_CHANGE, 10.0) in result.anomalies
    assert (FLAG_UNMATCHED_IMPACT, 12.0) in result.anomalies


def test_impact_outside_the_window_does_not_bind():
    result = bind_shots(panel_changes=[100.0], impacts=[80.0], window_s=10.0)
    assert result.shots == []
    assert (FLAG_UNMATCHED_PANEL_CHANGE, 100.0) in result.anomalies
    assert (FLAG_UNMATCHED_IMPACT, 80.0) in result.anomalies


def test_impact_at_the_window_edge_binds():
    result = bind_shots(panel_changes=[90.0], impacts=[80.0], window_s=10.0)
    assert len(result.shots) == 1


def test_nearest_preceding_impact_wins():
    # Two swings before one refresh: the refresh describes the most recent one.
    result = bind_shots(panel_changes=[20.0], impacts=[12.0, 18.0])
    assert len(result.shots) == 1
    assert result.shots[0].impact_ts == 18.0
    assert (FLAG_UNMATCHED_IMPACT, 12.0) in result.anomalies


def test_an_impact_is_claimed_only_once():
    # Two panel refreshes cannot both describe the same swing -- a spurious
    # second refresh must surface as an anomaly, not duplicate a shot.
    result = bind_shots(panel_changes=[12.0, 13.0], impacts=[10.0])
    assert len(result.shots) == 1
    assert result.shots[0].impact_ts == 10.0
    assert (FLAG_UNMATCHED_PANEL_CHANGE, 13.0) in result.anomalies


def test_quick_double_swing_pairs_in_order():
    # Golfer hits twice in quick succession; both refreshes arrive after.
    result = bind_shots(panel_changes=[14.0, 17.0], impacts=[10.0, 15.0])
    pairs = [(s.impact_ts, s.panel_change_ts) for s in result.shots]
    assert pairs == [(10.0, 14.0), (15.0, 17.0)]
    assert result.anomalies == []


def test_final_shot_lost_when_recording_stops_early():
    # Spec section 12.1: the last shot is dropped if the panel never refreshes.
    # It must be reported, not silently discarded.
    result = bind_shots(panel_changes=[12.0], impacts=[10.0, 60.0])
    assert len(result.shots) == 1
    assert (FLAG_UNMATCHED_IMPACT, 60.0) in result.anomalies


def test_unsorted_input_is_handled():
    result = bind_shots(panel_changes=[45.0, 12.0], impacts=[43.0, 10.0])
    assert [s.impact_ts for s in result.shots] == [10.0, 43.0]


def test_empty_input():
    result = bind_shots([], [])
    assert result.shots == []
    assert result.anomalies == []
