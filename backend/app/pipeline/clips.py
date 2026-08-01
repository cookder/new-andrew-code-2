"""Stage 7 -- clip extraction.

"For each shot, cut a video clip from ``impact_ts - 4s`` to ``impact_ts + 2s``.
Every stat row should be one click from watching the swing that produced it."

The clip is anchored to impact, not to the panel change, so it contains the
swing rather than the golfer reading their numbers.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from ..config import settings
from ..constants import CLIP_AFTER_S, CLIP_BEFORE_S
from .. import store


class ClipError(RuntimeError):
    pass


def cut_clip(
    video_path: Path | str,
    out_path: Path | str,
    impact_ts: float,
    *,
    before: float = CLIP_BEFORE_S,
    after: float = CLIP_AFTER_S,
) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, impact_ts - before)
    duration = before + after if impact_ts - before >= 0 else impact_ts + after

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        # Seek before -i for speed, then re-encode so the clip starts on a real
        # frame rather than the preceding keyframe.
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(out),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise ClipError("ffmpeg is not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise ClipError(f"clip extraction failed: {exc.stderr.strip()}") from exc
    return out


def extract_session_clips(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    """Cut one clip per shot and record it as an artifact."""
    session = store.get_session(conn, session_id)
    if session is None:
        raise ClipError(f"no session {session_id}")
    video = session.get("source_video_path")
    if not video or not Path(video).is_file():
        raise ClipError("source video is no longer on disk; clips cannot be re-cut")

    out_dir = settings.artifact_dir / f"session-{session_id}"
    artifacts = []
    for shot in store.list_shots(conn, session_id):
        if shot.get("impact_ts") is None:
            continue
        path = cut_clip(
            video, out_dir / f"shot-{shot['id']}-clip.mp4", float(shot["impact_ts"])
        )
        artifacts.append(store.add_artifact(conn, shot["id"], "clip", path))
    return artifacts
