"""Generate a synthetic range-session video with known ground truth.

Built to the geometry measured off real session frames: a 1080x1920 portrait
frame, a bright projected screen at x 363-853 / y 902-1267 in a dark bay, and
the stat panel occupying the leftmost ~21% of that screen at near-full height
(so the panel comes out about 103x362 px, against ~94x347 measured).

Two details are deliberately reproduced because they are what break naive
implementations:

* The panel header carries a **clock that ticks every second**. Any change
  detector that diffs the whole panel will fire on every sample.
* The panel updates ~2.5s *after* the impact that produced it, and shows the
  previous shot's numbers -- the timing behaviour the whole binding stage
  exists to handle.

Ground truth is returned alongside the file so tests can assert against it.
"""

from __future__ import annotations

import math
import struct
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

# Measured geometry, in a 1080x1920 frame.
FRAME_W, FRAME_H = 1080, 1920
SCREEN = (363, 902, 853, 1267)  # left, top, right, bottom
PANEL_WIDTH_FRACTION = 0.21
PANEL_HEADER_PX = 30

FPS = 30
PANEL_LAG_S = 2.5  # impact -> panel refresh


@dataclass
class SyntheticSession:
    path: Path
    impacts: list[float]
    panel_changes: list[float]
    duration_s: float
    shots: list[dict] = field(default_factory=list)


#: Six shots' worth of plausible values, in panel-grid order.
SHOT_VALUES = [
    ["230.6", "265.7", "156.2", "104.9", "1.49", "53.0", "2220", "14", "1.8", "-2.5", "4.3", "8.9"],
    ["189.7", "210.1", "122.9", "93.1", "1.32", "75.6", "2193", "1", "-2.0", "-0.6", "-1.3", "17.6"],
    ["210.6", "233.1", "135.6", "101.3", "1.34", "75.8", "2234", "10", "4.6", "1.6", "3.0", "15.1"],
    ["176.4", "192.0", "118.0", "89.4", "1.32", "71.2", "6810", "-3", "0.4", "1.9", "-1.5", "18.8"],
    ["164.2", "170.9", "112.7", "85.1", "1.32", "68.0", "7020", "5", "2.2", "-0.8", "3.0", "19.4"],
    ["201.3", "221.8", "129.4", "97.6", "1.33", "74.1", "2410", "8", "-1.1", "0.7", "-1.8", "16.2"],
]


def _draw_frame(t: float, value_index: int):
    """One frame at time ``t`` showing ``SHOT_VALUES[value_index]``."""
    import cv2
    import numpy as np

    frame = np.full((FRAME_H, FRAME_W, 3), 18, dtype=np.uint8)

    left, top, right, bottom = SCREEN
    # Projected screen: bright, with a sky/turf split so it is not flat.
    cv2.rectangle(frame, (left, top), (right, bottom), (150, 120, 90), -1)
    horizon = top + int((bottom - top) * 0.45)
    cv2.rectangle(frame, (left, horizon), (right, bottom), (120, 175, 150), -1)

    # A moving ball-flight arc on the right of the screen, to prove the diff
    # is confined to the panel and not tripped by screen animation.
    phase = (t * 0.7) % 1.0
    cx = int(left + (right - left) * (0.35 + 0.5 * phase))
    cy = int(horizon - 60 * math.sin(math.pi * phase))
    cv2.circle(frame, (cx, cy), 4, (255, 255, 255), -1)

    # Stat panel.
    panel_right = left + int((right - left) * PANEL_WIDTH_FRACTION)
    cv2.rectangle(frame, (left, top), (panel_right, bottom), (40, 32, 28), -1)

    # Header: hamburger, shot counter, and a clock that ticks every second.
    clock = f"{int(t) // 60:02d}:{int(t) % 60:02d}"
    cv2.putText(frame, "=", (left + 4, top + 20), cv2.FONT_HERSHEY_SIMPLEX,
                0.4, (220, 220, 220), 1, cv2.LINE_AA)
    cv2.putText(frame, f"{value_index + 1} Shots", (left + 18, top + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.28, (210, 210, 210), 1, cv2.LINE_AA)
    cv2.putText(frame, clock, (left + 66, top + 20), cv2.FONT_HERSHEY_SIMPLEX,
                0.28, (120, 230, 140), 1, cv2.LINE_AA)

    # 2x6 value grid below the header.
    values = SHOT_VALUES[value_index]
    grid_top = top + PANEL_HEADER_PX
    cell_w = (panel_right - left) // 2
    cell_h = (bottom - grid_top) // 6
    for i, value in enumerate(values):
        row, col = divmod(i, 2)
        x = left + col * cell_w + 6
        y = grid_top + row * cell_h + int(cell_h * 0.55)
        # Bottom row renders faded, as the real panel does.
        shade = 110 if row == 5 else 245
        cv2.putText(frame, value, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, (shade, shade, shade), 1, cv2.LINE_AA)
    return frame


def _write_audio(path: Path, impacts: list[float], duration_s: float, rate: int = 16000):
    """Room tone plus a sharp decaying transient at each impact."""
    import numpy as np

    n = int(duration_s * rate)
    rng = np.random.default_rng(11)
    signal = rng.normal(0, 0.006, n)  # room tone

    # Low-frequency hum, so onset detection has something to reject.
    t = np.arange(n) / rate
    signal += 0.01 * np.sin(2 * np.pi * 60 * t)

    for impact in impacts:
        start = int(impact * rate)
        length = int(0.09 * rate)
        if start + length > n:
            continue
        env = np.exp(-np.linspace(0, 14, length))
        burst = rng.normal(0, 1.0, length) * env
        signal[start : start + length] += burst

    signal = np.clip(signal, -1.0, 1.0)
    pcm = (signal * 32767).astype("<i2")

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())


def build(out_path: Path | str, *, shots: int = 4, spacing_s: float = 12.0) -> SyntheticSession:
    """Render a synthetic session and return it with its ground truth."""
    import cv2

    from backend.app.pipeline.media import ffmpeg_path

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    impacts = [8.0 + i * spacing_s for i in range(shots)]
    panel_changes = [i + PANEL_LAG_S for i in impacts]
    duration = panel_changes[-1] + 6.0

    silent = out.with_suffix(".silent.mp4")
    writer = cv2.VideoWriter(
        str(silent), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (FRAME_W, FRAME_H)
    )
    if not writer.isOpened():
        raise RuntimeError("could not open the video writer")

    try:
        for index in range(int(duration * FPS)):
            t = index / FPS
            # The panel shows the PREVIOUS shot: it advances only once a refresh
            # time has passed.
            shown = sum(1 for change in panel_changes if t >= change)
            writer.write(_draw_frame(t, min(shown, len(SHOT_VALUES) - 1)))
    finally:
        writer.release()

    audio = out.with_suffix(".wav")
    _write_audio(audio, impacts, duration)

    subprocess.run(
        [ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(silent), "-i", str(audio),
         "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True, text=True,
    )
    silent.unlink(missing_ok=True)
    audio.unlink(missing_ok=True)

    return SyntheticSession(
        path=out,
        impacts=impacts,
        panel_changes=panel_changes,
        duration_s=duration,
        shots=[dict(zip(
            ["carry", "total", "ball_speed", "club_speed", "smash_factor", "apex",
             "spin_rate", "spin_axis", "face_angle", "club_path", "face_to_path",
             "launch_angle"],
            SHOT_VALUES[i + 1], strict=True)) for i in range(shots)],
    )
