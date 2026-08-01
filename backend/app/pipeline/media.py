"""Locating ffmpeg/ffprobe, and probing video without requiring ffprobe.

A system ffmpeg install is the normal case, but it is not always available --
a locked-down work machine may not permit installing one, and the pip-installed
``imageio-ffmpeg`` wheel bundles a static ffmpeg binary that works fine. It does
not bundle ffprobe, so the metadata path falls back to OpenCV, which reports
everything ingest actually needs.

Resolution order for each binary: an explicit environment override, then PATH,
then the bundled wheel.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path


class MediaToolError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    override = os.environ.get("GOLF_FFMPEG")
    if override:
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise MediaToolError(
            "no ffmpeg available: install one, or `pip install imageio-ffmpeg`, "
            "or set GOLF_FFMPEG to a binary"
        ) from exc


@lru_cache(maxsize=1)
def ffprobe_path() -> str | None:
    """ffprobe if we have one, else None -- callers fall back to OpenCV."""
    override = os.environ.get("GOLF_FFPROBE")
    if override:
        return override
    return shutil.which("ffprobe")


def probe_with_opencv(video_path: Path | str) -> dict:
    """Duration, fps and resolution straight from the container, via OpenCV.

    Everything ingest needs except the creation timestamp, which the caller
    already falls back to file mtime for.
    """
    import cv2

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise MediaToolError(f"could not open video: {video_path}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        capture.release()

    if width <= 0 or height <= 0:
        raise MediaToolError(f"no readable video stream in {video_path}")

    return {
        "duration_s": round(frames / fps, 3) if fps else 0.0,
        "fps": round(fps, 3),
        "resolution": f"{width}x{height}",
        "recorded_at": None,
    }


def has_audio_stream(video_path: Path | str) -> bool:
    """True if the container carries an audio track.

    Worth checking before phase 2 does anything: without audio there is no
    impact onset, no transcript, and therefore no strike location -- which is
    the single highest-value field in the system.
    """
    import subprocess

    result = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-i", str(video_path)],
        capture_output=True,
        text=True,
    )
    # ffmpeg exits non-zero when given no output target; the stream listing it
    # prints on stderr is what we are after.
    return "Audio:" in result.stderr
