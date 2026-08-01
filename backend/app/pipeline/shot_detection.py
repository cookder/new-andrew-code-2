"""Stage 3 -- shot detection.

The whole design of this stage follows from one fact in spec section 1:

    The on-screen stat panel displays the PREVIOUS completed shot, and updates
    roughly 2-3 seconds after impact.

So the panel is never read at swing time. A panel change is the authoritative
"a shot completed" event, and it binds *backwards* to the swing that caused it.

Two independent signals:

* **Panel change (authoritative).** Sample the canonicalized panel crop at 2fps
  and perceptually diff consecutive samples. A diff above threshold means a shot
  completed.
* **Audio onset (timestamping).** Onset detection over the full-rate audio
  locates the impact transient, which is what the clip and the transcript window
  are anchored to.

The binding logic below is pure and has no media dependencies, so it is unit
tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..constants import (
    BIND_WINDOW_S,
    CANONICAL_PANEL_SIZE,
    FLAG_UNMATCHED_IMPACT,
    FLAG_UNMATCHED_PANEL_CHANGE,
    ONSET_BASELINE_SIGMAS,
    ONSET_PEAK_DELTA,
    ONSET_RELATIVE_FLOOR,
    PANEL_CHANGE_THRESHOLD,
    PANEL_DIFF_SIZE,
    PANEL_PIXEL_DELTA,
    PANEL_SAMPLE_FPS,
)
from .calibration import header_height


@dataclass
class Shot:
    """One bound (impact, panel change) pair."""

    shot_index: int
    impact_ts: float
    panel_change_ts: float


@dataclass
class DetectionResult:
    shots: list[Shot] = field(default_factory=list)
    #: (kind, ts) pairs for panel changes and impacts that never paired up.
    anomalies: list[tuple[str, float]] = field(default_factory=list)


def bind_shots(
    panel_changes: list[float],
    impacts: list[float],
    *,
    window_s: float = BIND_WINDOW_S,
) -> DetectionResult:
    """Bind each panel change to the nearest *preceding* impact.

    Spec: "each ``panel_change_ts`` binds to the nearest preceding ``impact_ts``
    within a 10 second window. Unmatched panel changes and unmatched impacts are
    both logged as anomalies for review."

    An impact can only be claimed once -- two panel refreshes cannot both belong
    to the same swing. Processing panel changes in time order and always taking
    the *latest* eligible unclaimed impact gives the correct pairing when the
    golfer swings twice before the panel catches up.
    """
    result = DetectionResult()
    unclaimed = sorted(impacts)
    claimed: set[int] = set()

    for change_ts in sorted(panel_changes):
        candidate: int | None = None
        for i, impact_ts in enumerate(unclaimed):
            if i in claimed:
                continue
            if impact_ts > change_ts:
                break  # impacts are sorted; nothing later can precede the change
            if change_ts - impact_ts <= window_s:
                candidate = i  # keep walking to find the latest eligible one
        if candidate is None:
            result.anomalies.append((FLAG_UNMATCHED_PANEL_CHANGE, change_ts))
            continue
        claimed.add(candidate)
        result.shots.append(
            Shot(
                shot_index=len(result.shots) + 1,
                impact_ts=unclaimed[candidate],
                panel_change_ts=change_ts,
            )
        )

    for i, impact_ts in enumerate(unclaimed):
        if i not in claimed:
            # Spec section 12.1: the final shot is lost if recording stops before
            # the panel refreshes. That shows up here, as a trailing unmatched
            # impact, and is reported rather than silently dropped.
            result.anomalies.append((FLAG_UNMATCHED_IMPACT, impact_ts))

    result.shots.sort(key=lambda s: s.panel_change_ts)
    for i, shot in enumerate(result.shots, start=1):
        shot.shot_index = i
    return result


# --------------------------------------------------------------------------
# Signal extraction (needs media libraries)
# --------------------------------------------------------------------------


def detect_panel_changes(
    video_path: Path | str,
    transform: list[list[float]],
    *,
    sample_fps: float = PANEL_SAMPLE_FPS,
    threshold: float = PANEL_CHANGE_THRESHOLD,
) -> list[float]:
    """Sample the canonicalized panel and return timestamps where it changed.

    The metric is the *fraction of pixels that visibly changed*, not the mean
    absolute difference. Mean-abs-diff is the obvious choice and it does not
    work here: the panel is mostly flat background, and a full set of new
    numbers is thin strokes covering a few percent of the area, so every real
    shot averages out to a diff of ~0.001 -- indistinguishable from compression
    noise, and two orders of magnitude below any threshold that would also
    reject noise.

    Counting changed pixels instead separates them cleanly, and it stays stable
    if the projector brightness drifts, since a global shift moves every pixel
    a little rather than a few pixels a lot.
    """
    import cv2  # lazy: opencv is only needed for phase 3
    import numpy as np

    matrix = np.array(transform, dtype=np.float32)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps / sample_fps)))
        changes: list[float] = []
        previous = None
        frame_index = 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % step == 0:
                panel = cv2.warpPerspective(frame, matrix, CANONICAL_PANEL_SIZE)
                # Drop the header before diffing. It carries the shot counter
                # and a badge that reads as a running clock -- a ticking clock
                # inside the diff region would cross the threshold on every
                # sample and report a shot roughly twice a second. The counter
                # changes at the same instant the values do, so excluding the
                # whole strip costs no signal.
                values_only = panel[header_height() :, :]
                gray = cv2.cvtColor(values_only, cv2.COLOR_BGR2GRAY)
                # Keep enough resolution that digit strokes survive; downscaling
                # to thumbnail size erases the very thing being detected.
                small = cv2.resize(gray, PANEL_DIFF_SIZE).astype("float32") / 255.0
                if previous is not None:
                    changed = float(
                        np.mean(np.abs(small - previous) > PANEL_PIXEL_DELTA)
                    )
                    if changed > threshold:
                        changes.append(frame_index / fps)
                previous = small
            frame_index += 1
        return changes
    finally:
        cap.release()


def detect_impacts(
    audio_path: Path | str,
    *,
    min_separation_s: float = 2.0,
) -> list[float]:
    """Onset detection over the session audio, returning impact timestamps.

    A struck golf ball is a short broadband transient, which is exactly what
    spectral-flux onset detection is good at. Speech is filtered out by the
    minimum separation and by requiring a strong onset envelope peak -- the
    golfer talks between shots, not during them.
    """
    import librosa  # lazy: phase 2 only
    import numpy as np

    hop = 512
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    envelope = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    # NOTE ON `delta`: onset_detect max-normalizes the envelope to 0-1 before
    # peak picking, so delta is a fraction of the *loudest onset in the whole
    # session*, not an absolute strength. A large value therefore keeps only
    # near-loudest impacts and silently drops the rest -- at 0.6 a thinned shot
    # 80% as loud as the session's best strike vanishes, and mishits are exactly
    # the shots the strike-location analysis exists to study. (A delta >= 1.0
    # returns nothing at all, which is the giveaway.)
    #
    # Keep peak picking permissive here and do the speech rejection below,
    # where the criterion is explicit and inspectable.
    frames = librosa.onset.onset_detect(
        onset_envelope=envelope,
        sr=sr,
        hop_length=hop,
        backtrack=False,
        units="frames",
        delta=ONSET_PEAK_DELTA,
        wait=int(min_separation_s * sr / hop),
    )
    if len(frames) == 0:
        return []

    strengths = envelope[frames]

    # Reject the quiet onsets that speech produces, keep the impacts.
    #
    # The reference has to be the *envelope's* distribution and the loudest
    # onset -- not the detected onsets' own median and standard deviation.
    # Impacts within a session are of comparable strength, so a median+sigma
    # cutoff computed over a clean set of impacts sits above most of them and
    # discards real shots (it kept 1 of 4 on a signal with four identical
    # transients).
    baseline = float(np.median(envelope))
    loudest = float(strengths.max())
    cutoff = max(
        baseline + ONSET_BASELINE_SIGMAS * float(np.std(envelope)),
        baseline + ONSET_RELATIVE_FLOOR * (loudest - baseline),
    )
    keep = [f for f, s in zip(frames, strengths, strict=True) if s >= cutoff]
    return [float(t) for t in librosa.frames_to_time(keep, sr=sr, hop_length=hop)]
