"""Single source of truth for metrics, units, panel layout, and enums.

Spec section 2: "Store units explicitly in the schema or in a single constants
module. Never let a unit be implied by context."  This is that module. Nothing
else in the codebase should hardcode a unit string, a tolerance, or a grid
position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    #: Range sanity bounds from spec section 3. Flag outside these, never reject.
    sane_min: float | None = None
    sane_max: float | None = None
    #: Spin rate and spin axis read zero on this unit. Zero means "not measured".
    zero_is_missing: bool = False
    #: Derived fields are never stored from OCR; they are computed.
    derived: bool = False
    decimals: int = 1


METRICS: tuple[Metric, ...] = (
    Metric("carry", "Carry", "yards", 0, 400, decimals=1),
    Metric("total", "Total", "yards", 0, 450, decimals=1),
    Metric("ball_speed", "Ball speed", "mph", 0, 220, decimals=1),
    Metric("club_speed", "Club speed", "mph", 0, 140, decimals=1),
    Metric("smash_factor", "Smash factor", "ratio", 0.8, 1.55, decimals=2),
    Metric("apex", "Apex", "feet", 0, 250, decimals=0),
    Metric("spin_rate", "Spin rate", "rpm", 0, 12000, zero_is_missing=True, decimals=0),
    Metric("spin_axis", "Spin axis", "degrees", -45, 45, zero_is_missing=True, decimals=1),
    Metric("face_angle", "Face angle", "degrees", -20, 20, decimals=1),
    Metric("club_path", "Club path", "degrees", -20, 20, decimals=1),
    Metric("face_to_path", "Face to path", "degrees", -25, 25, derived=True, decimals=1),
    Metric("launch_angle", "Launch angle", "degrees", -5, 60, decimals=1),
)

METRICS_BY_KEY: dict[str, Metric] = {m.key: m for m in METRICS}
METRIC_KEYS: tuple[str, ...] = tuple(m.key for m in METRICS)

#: Fields an OCR pass or a human may write. face_to_path is excluded on
#: purpose -- it is a generated column in SQLite and cannot be written.
WRITABLE_METRIC_KEYS: tuple[str, ...] = tuple(m.key for m in METRICS if not m.derived)

UNITS: dict[str, str] = {m.key: m.unit for m in METRICS}


# --------------------------------------------------------------------------
# Stat panel layout (spec section 2)
# --------------------------------------------------------------------------

#: Fixed 2x6 grid on the canonicalized panel, read left to right, top to bottom.
#: Read by grid position, NEVER by caption -- the captions are too small to OCR.
PANEL_GRID: tuple[tuple[str, str], ...] = (
    ("carry", "total"),
    ("ball_speed", "club_speed"),
    ("smash_factor", "apex"),
    ("spin_rate", "spin_axis"),
    ("face_angle", "club_path"),
    ("face_to_path", "launch_angle"),
)

#: Row 6 renders with a fade gradient and is frequently illegible. Cells in this
#: row get CLAHE contrast stretch before OCR, and low-confidence handling after.
FADED_ROWS: frozenset[int] = frozenset({5})  # zero-indexed row 6

#: Canonical panel rectangle that calibration warps the detected panel onto.
#:
#: Sized to the panel's real proportions, measured off session frames: the panel
#: runs about 94x347 px in a 1080-wide portrait frame, i.e. h/w ~ 3.7. Warping a
#: 3.7:1 source onto a 2.1:1 canvas would stretch every digit horizontally by
#: nearly 2x before OCR, so the canvas matches the source aspect instead.
CANONICAL_PANEL_SIZE: tuple[int, int] = (400, 1400)  # (width, height) px

#: Plausible h/w range for the stat panel. Measured 3.4-3.7 across frames; the
#: window is deliberately generous because perspective from an off-axis camera
#: shears the apparent ratio.
PANEL_ASPECT_RANGE: tuple[float, float] = (2.6, 4.8)

#: The panel's top strip is a header (hamburger menu, shot counter, and a badge
#: that appears to be a running clock) sitting above the 2x6 value grid. It is
#: excluded from both the grid and the change-detection diff -- see
#: PANEL_HEADER_FRACTION's use in shot_detection.
PANEL_HEADER_FRACTION: float = 0.09

#: Where the stat panel sits inside the *screen*, as fractions of the
#: rectified screen (left, top, right, bottom).
#:
#: The panel itself has no crisp border -- it is a translucent overlay on a
#: bright projected image -- but the projected screen is a bright quadrilateral
#: in a dark room and is trivially detectable. Locating the screen first and
#: taking a fixed fraction of it is far more robust, and the fractions are
#: camera-invariant once the screen has been rectified.
PANEL_BOUNDS_IN_SCREEN: tuple[float, float, float, float] = (0.0, 0.01, 0.21, 1.0)


def grid_position(metric_key: str) -> tuple[int, int]:
    """(row, col) of a metric in the stat panel, zero-indexed."""
    for r, row in enumerate(PANEL_GRID):
        for c, key in enumerate(row):
            if key == metric_key:
                return r, c
    raise KeyError(f"{metric_key} is not on the stat panel")


# --------------------------------------------------------------------------
# Validation tolerances (spec section 3)
# --------------------------------------------------------------------------

SMASH_TOLERANCE = 0.02
FACE_TO_PATH_TOLERANCE = 0.2

#: Ball-speed deviation from the trailing median that trips the unannounced
#: club change guard (spec section 6, stage 6).
CLUB_CHANGE_BALL_SPEED_MPH = 12.0
CLUB_CHANGE_TRAILING_SHOTS = 3


# --------------------------------------------------------------------------
# Shot detection / binding (spec section 4, stage 3)
# --------------------------------------------------------------------------

#: The panel shows the PREVIOUS shot and refreshes 2-3s after impact, so a
#: panel change binds backwards to the nearest preceding impact.
BIND_WINDOW_S = 10.0
#: Grab the stat frame slightly after the change so the panel has settled.
STAT_FRAME_OFFSET_S = 0.5
#: Panel diff sampling rate.
PANEL_SAMPLE_FPS = 2.0
#: Working resolution for the panel change diff. Large enough that digit
#: strokes survive the resize -- a thumbnail erases them.
PANEL_DIFF_SIZE: tuple[int, int] = (100, 350)  # (width, height)
#: A pixel counts as changed once it moves this far on a 0-1 grayscale scale.
PANEL_PIXEL_DELTA = 0.15
#: Fraction of panel pixels that must change to call it a completed shot.
#: A fresh set of numbers moves a few percent of the panel; compression noise
#: and the ball-flight animation move far less.
PANEL_CHANGE_THRESHOLD = 0.02
#: Peak-picking sensitivity for onset detection. This is a fraction of the
#: loudest onset in the session, because librosa max-normalizes the envelope
#: first -- keep it small so quiet mishits survive to the explicit filter below.
ONSET_PEAK_DELTA = 0.08
#: Onset filtering. An impact must clear the envelope baseline by this many
#: standard deviations...
ONSET_BASELINE_SIGMAS = 2.0
#: ...and be at least this fraction as strong as the loudest onset in the
#: session. Impacts are of comparable loudness; speech is markedly quieter.
#: Both are tuned on synthetic audio and will need revisiting against a real
#: recording -- see HANDOFF.md.
ONSET_RELATIVE_FLOOR = 0.3
#: Transcript window around impact handed to the voice-attribution LLM.
VOICE_WINDOW_BEFORE_S = 15.0
VOICE_WINDOW_AFTER_S = 20.0
#: Clip bounds around impact.
CLIP_BEFORE_S = 4.0
CLIP_AFTER_S = 2.0


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------

SessionStatus = Literal["processing", "needs_calibration", "extracted", "confirmed", "failed"]
SESSION_STATUSES: tuple[str, ...] = (
    "processing",
    "needs_calibration",
    "extracted",
    "confirmed",
    "failed",
)

ClubSource = Literal["stated", "inherited", "manual"]
CLUB_SOURCES: tuple[str, ...] = ("stated", "inherited", "manual")

Confidence = Literal["high", "needs_review"]
CONFIDENCES: tuple[str, ...] = ("high", "needs_review")

StrikeLocation = Literal["heel", "toe", "center", "high", "low", "combination"]
STRIKE_LOCATIONS: tuple[str, ...] = ("heel", "toe", "center", "high", "low", "combination")

ArtifactKind = Literal["stat_frame", "clip", "transcript_segment"]
ARTIFACT_KINDS: tuple[str, ...] = ("stat_frame", "clip", "transcript_segment")


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------

FLAG_SMASH_MISMATCH = "smash_mismatch"
FLAG_FACE_TO_PATH_MISMATCH = "face_to_path_mismatch"
FLAG_OUT_OF_RANGE = "out_of_range"
FLAG_MISSING_METRIC = "missing_metric"
FLAG_ZERO_SPIN = "zero_spin"
FLAG_LOW_CONFIDENCE_OCR = "low_confidence"
FLAG_SUSPECTED_CLUB_CHANGE = "suspected_club_change"
FLAG_UNMATCHED_PANEL_CHANGE = "unmatched_panel_change"
FLAG_UNMATCHED_IMPACT = "unmatched_impact"
FLAG_NO_CLUB = "no_club"

#: Human-readable reason shown in the review UI. Section 6: a flagged row must
#: name the specific failure, not just say "needs review".
FLAG_DESCRIPTIONS: dict[str, str] = {
    FLAG_SMASH_MISMATCH: "Smash factor does not match ball speed / club speed",
    FLAG_FACE_TO_PATH_MISMATCH: "OCR'd face-to-path disagrees with face angle - club path",
    FLAG_OUT_OF_RANGE: "A metric is outside its plausible range",
    FLAG_MISSING_METRIC: "One or more metrics could not be read",
    FLAG_ZERO_SPIN: "Spin read zero; treated as missing, not measured",
    FLAG_LOW_CONFIDENCE_OCR: "OCR reported low confidence (usually the faded bottom row)",
    FLAG_SUSPECTED_CLUB_CHANGE: "Ball speed jumped; club may have changed without being announced",
    FLAG_UNMATCHED_PANEL_CHANGE: "Panel updated with no preceding impact in the bind window",
    FLAG_UNMATCHED_IMPACT: "Impact detected with no following panel update",
    FLAG_NO_CLUB: "No club stated and none to inherit",
}


# --------------------------------------------------------------------------
# Club vocabulary (spec stage 5: hint passed to Whisper)
# --------------------------------------------------------------------------

CLUB_VOCABULARY: tuple[str, ...] = (
    "driver",
    "3 wood",
    "5 wood",
    "7 wood",
    "2 hybrid",
    "3 hybrid",
    "4 hybrid",
    "5 hybrid",
    "3 iron",
    "4 iron",
    "5 iron",
    "6 iron",
    "7 iron",
    "8 iron",
    "9 iron",
    "pitching wedge",
    "gap wedge",
    "sand wedge",
    "lob wedge",
    "50 degree",
    "52 degree",
    "54 degree",
    "56 degree",
    "58 degree",
    "60 degree",
)

#: Loft/length ordering, used only for display. Not a semantic ranking.
CLUB_DISPLAY_ORDER: dict[str, int] = {club: i for i, club in enumerate(CLUB_VOCABULARY)}


# --------------------------------------------------------------------------
# Analysis gating (spec section 7)
# --------------------------------------------------------------------------

#: "Do not surface a trend line until a club has at least 30 confirmed shots
#: across at least 3 sessions." Arbitrary per section 12.4; tune with volume.
TREND_MIN_SHOTS = 30
TREND_MIN_SESSIONS = 3


# --------------------------------------------------------------------------
# Storage (spec section 10)
# --------------------------------------------------------------------------

#: Retain source video this long after review confirmation, then PROMPT.
#: Never auto-delete.
SOURCE_RETENTION_DAYS = 30
