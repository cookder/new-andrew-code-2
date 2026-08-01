"""Stage 1 -- ingest.

Watched folder. On a new MP4: probe it, extract audio, create the session row.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from .. import store
from .media import ffmpeg_path, ffprobe_path, probe_with_opencv


class IngestError(RuntimeError):
    pass


def _mtime_iso(video_path: Path | str) -> str:
    """Fall back to the file's mtime rather than "now" -- a video copied off a
    phone weeks later should still date to when it was shot."""
    return datetime.fromtimestamp(
        Path(video_path).stat().st_mtime, tz=timezone.utc
    ).isoformat(timespec="seconds")


def probe(video_path: Path | str) -> dict:
    """Duration, resolution, fps, creation timestamp.

    Prefers ffprobe, which is the only source for the container's real creation
    timestamp. Falls back to OpenCV when ffprobe is absent -- a bundled
    ``imageio-ffmpeg`` gives us ffmpeg but not ffprobe, and that combination
    should still be able to ingest.
    """
    probe_bin = ffprobe_path()
    if probe_bin is None:
        meta = probe_with_opencv(video_path)
        meta["recorded_at"] = _mtime_iso(video_path)
        return meta

    cmd = [
        probe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as exc:
        raise IngestError(f"ffprobe failed: {exc.stderr.strip()}") from exc

    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise IngestError(f"no video stream in {video_path}")

    num, _, den = video.get("r_frame_rate", "30/1").partition("/")
    fps = float(num) / float(den or 1)

    fmt = data.get("format", {})
    return {
        "duration_s": float(fmt.get("duration", 0.0)),
        "fps": round(fps, 3),
        "resolution": f"{video['width']}x{video['height']}",
        "recorded_at": fmt.get("tags", {}).get("creation_time") or _mtime_iso(video_path),
    }


def extract_audio(video_path: Path | str, out_path: Path | str) -> Path:
    """16kHz mono WAV -- what faster-whisper wants, and enough for onset detection."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(out),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise IngestError(f"audio extraction failed: {exc.stderr.strip()}") from exc
    return out


def ingest_video(
    conn: sqlite3.Connection, video_path: Path | str, *, copy_source: bool = True
) -> dict:
    """Probe, extract audio, and create the session row in status ``processing``."""
    src = Path(video_path)
    if not src.is_file():
        raise IngestError(f"no such file: {src}")

    settings.ensure_dirs()
    meta = probe(src)

    session = store.create_session(
        conn,
        source_filename=src.name,
        status="processing",
        **meta,
    )
    sid = session["id"]

    stored = settings.media_dir / f"session-{sid}{src.suffix}"
    if copy_source and src.resolve() != stored.resolve():
        shutil.move(str(src), stored)
    else:
        stored = src

    audio = extract_audio(stored, settings.media_dir / f"session-{sid}.wav")
    return store.update_session(  # type: ignore[return-value]
        conn, sid, source_video_path=str(stored), audio_path=str(audio)
    )
