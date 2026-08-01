"""Spec section 3 -- arithmetic identities and range sanity.

Two identities hold on every valid shot and cost nothing to check, so they act
as free OCR verification:

    smash_factor ~= ball_speed / club_speed     (tolerance +/-0.02)
    face_to_path ~= face_angle - club_path      (tolerance +/-0.2)

A shot failing either is flagged ``needs_review`` and surfaced in the review UI.
Nothing here ever silently corrects a value.
"""

from __future__ import annotations

from statistics import median

from .constants import (
    CLUB_CHANGE_BALL_SPEED_MPH,
    CLUB_CHANGE_TRAILING_SHOTS,
    FACE_TO_PATH_TOLERANCE,
    FLAG_FACE_TO_PATH_MISMATCH,
    FLAG_MISSING_METRIC,
    FLAG_NO_CLUB,
    FLAG_OUT_OF_RANGE,
    FLAG_SMASH_MISMATCH,
    FLAG_SUSPECTED_CLUB_CHANGE,
    FLAG_ZERO_SPIN,
    METRICS,
    METRICS_BY_KEY,
    SMASH_TOLERANCE,
    WRITABLE_METRIC_KEYS,
)

Number = float | int | None


def normalize_metrics(values: dict[str, Number]) -> tuple[dict[str, float | None], list[str]]:
    """Coerce a raw metric dict into storable values.

    Spec section 3: "spin rate and spin axis have historically read zero on this
    unit. Treat zeros as missing, not as measurements." That conversion happens
    exactly once, here, so no downstream aggregate ever averages in a fake zero.

    Returns (cleaned values, flags).
    """
    cleaned: dict[str, float | None] = {}
    flags: list[str] = []
    zeroed: list[str] = []

    for key in WRITABLE_METRIC_KEYS:
        raw = values.get(key)
        if raw is None or raw == "":
            cleaned[key] = None
            continue
        try:
            num = float(raw)
        except (TypeError, ValueError):
            cleaned[key] = None
            continue

        metric = METRICS_BY_KEY[key]
        if metric.zero_is_missing and num == 0:
            cleaned[key] = None
            zeroed.append(key)
            continue
        cleaned[key] = num

    if zeroed:
        flags.append(FLAG_ZERO_SPIN)
    return cleaned, flags


def check_smash(ball_speed: Number, club_speed: Number, smash_factor: Number) -> str | None:
    """smash_factor ~= ball_speed / club_speed, +/-0.02."""
    if ball_speed is None or club_speed is None or smash_factor is None:
        return None
    if club_speed == 0:
        return None  # division undefined; the missing-metric flag covers it
    expected = float(ball_speed) / float(club_speed)
    if abs(expected - float(smash_factor)) > SMASH_TOLERANCE:
        return FLAG_SMASH_MISMATCH
    return None


def check_face_to_path(
    face_angle: Number, club_path: Number, observed_face_to_path: Number
) -> str | None:
    """face_to_path ~= face_angle - club_path, +/-0.2.

    ``observed_face_to_path`` is an OCR reading of the row-6 left cell, passed
    in only when that cell was legible. The stored value is always the derived
    one (it is a generated column); this check exists purely to catch a bad read
    of face_angle or club_path when the panel happens to give us a third number
    to cross-check against. With no observation there is nothing to verify and
    the identity is true by construction.
    """
    if face_angle is None or club_path is None or observed_face_to_path is None:
        return None
    derived = float(face_angle) - float(club_path)
    if abs(derived - float(observed_face_to_path)) > FACE_TO_PATH_TOLERANCE:
        return FLAG_FACE_TO_PATH_MISMATCH
    return None


def check_ranges(values: dict[str, Number]) -> list[str]:
    """Range sanity per spec section 3. Flag, never reject."""
    out: list[str] = []
    for metric in METRICS:
        val = values.get(metric.key)
        if val is None:
            continue
        if metric.sane_min is not None and float(val) < metric.sane_min:
            out.append(f"{FLAG_OUT_OF_RANGE}:{metric.key}")
        elif metric.sane_max is not None and float(val) > metric.sane_max:
            out.append(f"{FLAG_OUT_OF_RANGE}:{metric.key}")
    return out


def validate_shot(
    values: dict[str, Number],
    *,
    club: str | None = None,
    observed_face_to_path: Number = None,
    require_all_metrics: bool = True,
) -> list[str]:
    """Full section-3 validation for one shot. Returns a flag list.

    ``values`` should already have been through :func:`normalize_metrics`.
    """
    flags: list[str] = []

    smash_flag = check_smash(
        values.get("ball_speed"), values.get("club_speed"), values.get("smash_factor")
    )
    if smash_flag:
        flags.append(smash_flag)

    ftp_flag = check_face_to_path(
        values.get("face_angle"), values.get("club_path"), observed_face_to_path
    )
    if ftp_flag:
        flags.append(ftp_flag)

    flags.extend(check_ranges(values))

    if require_all_metrics:
        # Spin is legitimately absent on this unit, so a missing spin value is
        # already reported by FLAG_ZERO_SPIN and does not also count as a hole.
        missing = [
            k
            for k in WRITABLE_METRIC_KEYS
            if values.get(k) is None and not METRICS_BY_KEY[k].zero_is_missing
        ]
        if missing:
            flags.append(FLAG_MISSING_METRIC + ":" + ",".join(missing))

    if not club:
        flags.append(FLAG_NO_CLUB)

    return flags


def derive_face_to_path(face_angle: Number, club_path: Number) -> float | None:
    """The one definition of face-to-path in the system.

    The database computes this as a generated column; this mirror exists for
    code that needs the value before a row is written (e.g. review previews).
    """
    if face_angle is None or club_path is None:
        return None
    # Rounded to match the generated column in schema.sql exactly.
    return round(float(face_angle) - float(club_path), 2)


# --------------------------------------------------------------------------
# Unannounced club change (spec stage 6)
# --------------------------------------------------------------------------


def suspected_club_change(
    ball_speed: Number,
    trailing_ball_speeds: list[float],
    *,
    threshold: float = CLUB_CHANGE_BALL_SPEED_MPH,
) -> bool:
    """True if this shot's ball speed deviates from the trailing median enough
    to suspect an unannounced club change.

    Club persistence (inherit the previous shot's club when none is stated)
    will silently mislabel an entire block if the golfer switches without
    saying so. The guard: >12 mph from the trailing 3-shot median for the
    inherited club. Flag only -- never auto-correct.
    """
    if ball_speed is None or not trailing_ball_speeds:
        return False
    window = [s for s in trailing_ball_speeds[-CLUB_CHANGE_TRAILING_SHOTS:] if s is not None]
    if not window:
        return False
    return abs(float(ball_speed) - median(window)) > threshold


def flag_unannounced_club_changes(shots: list[dict]) -> list[list[str]]:
    """Run the club-change guard across a session's shots in order.

    Returns a per-shot list of extra flags. The trailing window resets whenever
    the club is explicitly stated, because an announcement re-establishes truth
    and the speeds before it belong to a different club.
    """
    extra: list[list[str]] = []
    trailing: list[float] = []
    for shot in shots:
        flags: list[str] = []
        stated = shot.get("club_source") == "stated"
        if stated:
            trailing = []
        elif suspected_club_change(shot.get("ball_speed"), trailing):
            flags.append(FLAG_SUSPECTED_CLUB_CHANGE)
        bs = shot.get("ball_speed")
        if bs is not None:
            trailing.append(float(bs))
        extra.append(flags)
    return extra
