"""Pipeline orchestration.

Stages run in order and stop at the first hard failure. Deliberately a plain
function, not an agent framework: spec section 4 says "Deterministic stages. Two
LLM calls per shot. One agent, at the end only. Do not build a multi-agent
orchestration layer for this."

The two LLM calls per shot are stage 4 (panel OCR) and stage 6 (voice
attribution). The one agent is the phase 5 insight agent, which runs over the
confirmed dataset and never touches this path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .. import store
from ..constants import FLAG_UNMATCHED_IMPACT, FLAG_UNMATCHED_PANEL_CHANGE
from . import calibration, clips, ingest, shot_detection, stat_extraction, transcription, voice


@dataclass
class StageResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class PipelineResult:
    session_id: int | None = None
    stages: list[StageResult] = field(default_factory=list)
    halted: bool = False

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.stages.append(StageResult(name, ok, detail))


def run(
    conn: sqlite3.Connection,
    video_path: Path | str,
    *,
    with_clips: bool = True,
) -> PipelineResult:
    """Stages 1-7 for one video. Stage 8 (review) is a human, not a stage."""
    result = PipelineResult()

    # Stage 1 -- ingest
    try:
        session = ingest.ingest_video(conn, video_path)
    except Exception as exc:  # noqa: BLE001
        result.record("ingest", False, str(exc))
        result.halted = True
        return result
    result.session_id = session["id"]
    sid = session["id"]
    result.record("ingest", True, f"session {sid}")

    # Stage 2 -- calibration. Failure halts: never guess a crop.
    try:
        transform = calibration.calibrate(conn, sid)
    except calibration.CalibrationError as exc:
        result.record("calibration", False, str(exc))
        result.halted = True
        return result
    result.record("calibration", True, transform.get("source", "auto"))

    # Stage 3 -- shot detection
    try:
        panel_changes = shot_detection.detect_panel_changes(
            session["source_video_path"], transform["matrix"]
        )
        impacts = shot_detection.detect_impacts(session["audio_path"])
        detection = shot_detection.bind_shots(panel_changes, impacts)
    except Exception as exc:  # noqa: BLE001
        result.record("shot_detection", False, str(exc))
        store.update_session(conn, sid, status="failed", notes=str(exc))
        result.halted = True
        return result

    for kind, ts in detection.anomalies:
        detail = (
            "panel refreshed with no preceding swing in the bind window"
            if kind == FLAG_UNMATCHED_PANEL_CHANGE
            else "swing detected but the panel never refreshed (recording may have "
            "stopped before the last shot registered)"
        )
        store.add_anomaly(conn, sid, kind, ts, detail)

    shot_ids: list[tuple[int, float]] = []
    for shot in detection.shots:
        row = store.create_shot(
            conn,
            sid,
            shot_index=shot.shot_index,
            impact_ts=shot.impact_ts,
            panel_change_ts=shot.panel_change_ts,
        )
        shot_ids.append((row["id"], shot.panel_change_ts))
    result.record(
        "shot_detection",
        True,
        f"{len(detection.shots)} shots, {len(detection.anomalies)} anomalies",
    )

    # Stage 4 -- stat extraction (one LLM call per shot)
    ocr_failures = 0
    for shot_id, panel_ts in shot_ids:
        try:
            stat_extraction.extract_shot_stats(conn, sid, shot_id, panel_ts)
        except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the run
            ocr_failures += 1
            store.add_anomaly(conn, sid, "ocr_failed", panel_ts, str(exc))
    result.record(
        "stat_extraction", ocr_failures == 0, f"{ocr_failures} cells/shots failed"
    )

    # Stage 5 -- transcription
    try:
        transcript = transcription.transcribe(session["audio_path"])
        transcription.save_transcript(conn, sid, transcript)
        result.record("transcription", True, f"{len(transcript['words'])} words")
    except Exception as exc:  # noqa: BLE001
        result.record("transcription", False, str(exc))
        store.update_session(conn, sid, status="extracted")
        return result

    # Stage 6 -- voice attribution (the second LLM call per shot)
    try:
        voice.attribute_session(conn, sid)
        result.record("voice_attribution", True)
    except Exception as exc:  # noqa: BLE001
        result.record("voice_attribution", False, str(exc))

    # Stage 7 -- clips
    if with_clips:
        try:
            cut = clips.extract_session_clips(conn, sid)
            result.record("clips", True, f"{len(cut)} clips")
        except Exception as exc:  # noqa: BLE001
            result.record("clips", False, str(exc))

    # Stage 8 is the review UI. Nothing enters the confirmed dataset unreviewed,
    # so the session stops at 'extracted' and waits for a human.
    store.update_session(conn, sid, status="extracted")
    return result
