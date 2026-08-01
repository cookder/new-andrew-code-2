"""End-to-end media pipeline against a synthetic session with known ground truth.

Stages 1-3 and 7 run for real here: ingest probes the container and extracts
audio, calibration locates the screen and derives the panel, the change detector
diffs the panel over time, onset detection finds the impacts, and binding pairs
them. The synthetic session reproduces the geometry and the timing behaviour
measured off real frames, so a regression in any of those stages fails here
rather than at the driving range.

Stages 4-6 are not covered: they need an API key and a Whisper model download.

Skipped when OpenCV, ffmpeg, or librosa are absent, so the default suite still
runs on a bare install.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2", reason="phase 3 needs OpenCV")
pytest.importorskip("librosa", reason="phase 2 needs librosa")

from backend.app.db import connect, init_db  # noqa: E402
from backend.app.pipeline import calibration, ingest, shot_detection  # noqa: E402
from backend.app.pipeline.media import MediaToolError, ffmpeg_path, has_audio_stream  # noqa: E402
from backend.tests import synthetic  # noqa: E402

try:
    ffmpeg_path()
except MediaToolError:  # pragma: no cover
    pytest.skip("no ffmpeg available", allow_module_level=True)


SHOTS = 3
SPACING = 10.0


@pytest.fixture(scope="module")
def session_video(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic") / "session.mp4"
    return synthetic.build(out, shots=SHOTS, spacing_s=SPACING)


@pytest.fixture
def ingested(session_video, isolated_data_dir):
    inbox = isolated_data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    local = inbox / "session.mp4"
    shutil.copy(session_video.path, local)

    init_db()
    conn = connect()
    try:
        yield conn, ingest.ingest_video(conn, local), session_video
    finally:
        conn.close()


class TestIngest:
    def test_probes_without_ffprobe(self, session_video):
        # imageio-ffmpeg bundles ffmpeg but not ffprobe; ingest must still work.
        meta = ingest.probe(session_video.path)
        assert meta["resolution"] == "1080x1920"
        assert meta["fps"] == pytest.approx(30.0, abs=0.1)
        assert meta["duration_s"] == pytest.approx(session_video.duration_s, abs=1.0)

    def test_detects_the_audio_track(self, session_video):
        assert has_audio_stream(session_video.path) is True

    def test_extracts_audio(self, ingested):
        _, session, _ = ingested
        assert Path(session["audio_path"]).is_file()
        assert Path(session["audio_path"]).stat().st_size > 1000


class TestCalibration:
    def test_locates_the_panel(self, ingested):
        conn, session, _ = ingested
        transform = calibration.calibrate(conn, session["id"])
        left, top, right, bottom = synthetic.SCREEN
        panel_right = left + int((right - left) * synthetic.PANEL_WIDTH_FRACTION)

        (tl, tr, _, bl) = transform["corners"]
        assert tl[0] == pytest.approx(left, abs=12)
        assert tr[0] == pytest.approx(panel_right, abs=12)
        assert tl[1] == pytest.approx(top, abs=14)
        assert bl[1] == pytest.approx(bottom, abs=14)

    def test_does_not_crop_the_panel_off_its_own_left_edge(self, ingested):
        # Regression: thresholding for the *bright* screen excludes the dark
        # panel overlay, so the detected screen started exactly where the panel
        # ended and the panel crop contained sky. The recovered width is the
        # tell.
        conn, session, _ = ingested
        (tl, tr, _, _) = calibration.calibrate(conn, session["id"])["corners"]
        left, _, right, _ = synthetic.SCREEN
        expected = (right - left) * synthetic.PANEL_WIDTH_FRACTION
        assert tr[0] - tl[0] == pytest.approx(expected, rel=0.25)

    def test_persists_the_transform(self, ingested):
        conn, session, _ = ingested
        calibration.calibrate(conn, session["id"])
        from backend.app import store

        stored = store.get_session(conn, session["id"])["calibration_transform"]
        assert stored["source"] == "auto"
        assert len(stored["matrix"]) == 3


class TestPanelChangeDetection:
    def test_finds_every_panel_refresh(self, ingested):
        conn, session, truth = ingested
        transform = calibration.calibrate(conn, session["id"])
        changes = shot_detection.detect_panel_changes(
            session["source_video_path"], transform["matrix"]
        )
        assert len(changes) == SHOTS
        for found, expected in zip(changes, truth.panel_changes, strict=True):
            assert found == pytest.approx(expected, abs=1.0)

    def test_ticking_clock_does_not_register_as_a_shot(self, ingested):
        # The panel header carries a clock that advances every second. Diffing
        # the whole panel would report a shot on every sample -- roughly 100
        # false shots across this clip.
        conn, session, _ = ingested
        transform = calibration.calibrate(conn, session["id"])
        changes = shot_detection.detect_panel_changes(
            session["source_video_path"], transform["matrix"]
        )
        assert len(changes) == SHOTS

    def test_ball_flight_animation_does_not_register(self, ingested):
        # A mark moves across the screen continuously; it is outside the panel
        # crop and must not be picked up.
        conn, session, _ = ingested
        transform = calibration.calibrate(conn, session["id"])
        assert len(
            shot_detection.detect_panel_changes(
                session["source_video_path"], transform["matrix"]
            )
        ) == SHOTS


class TestImpactDetection:
    def test_finds_every_impact(self, ingested):
        # Regression: the old filter kept onsets above median+sigma *of the
        # detected onsets*, which discards real shots whenever they are of
        # similar loudness -- it kept 1 of 4 on a clean signal.
        _, session, truth = ingested
        impacts = shot_detection.detect_impacts(session["audio_path"])
        assert len(impacts) == SHOTS
        for found, expected in zip(impacts, truth.impacts, strict=True):
            assert found == pytest.approx(expected, abs=0.3)


class TestBinding:
    def test_binds_every_shot_with_no_anomalies(self, ingested):
        conn, session, truth = ingested
        transform = calibration.calibrate(conn, session["id"])
        changes = shot_detection.detect_panel_changes(
            session["source_video_path"], transform["matrix"]
        )
        impacts = shot_detection.detect_impacts(session["audio_path"])
        result = shot_detection.bind_shots(changes, impacts)

        assert result.anomalies == []
        assert len(result.shots) == SHOTS
        for shot, expected_impact in zip(result.shots, truth.impacts, strict=True):
            assert shot.impact_ts == pytest.approx(expected_impact, abs=0.3)

    def test_measured_lag_matches_the_spec(self, ingested):
        # Spec section 1: the panel updates roughly 2-3 seconds after impact.
        conn, session, _ = ingested
        transform = calibration.calibrate(conn, session["id"])
        result = shot_detection.bind_shots(
            shot_detection.detect_panel_changes(
                session["source_video_path"], transform["matrix"]
            ),
            shot_detection.detect_impacts(session["audio_path"]),
        )
        for shot in result.shots:
            assert 2.0 <= shot.panel_change_ts - shot.impact_ts <= 3.0
