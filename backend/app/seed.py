"""Seed three hand-entered sessions.

Spec section 8, phase 1: "Hand-enter three sessions. Build the database, review
UI, and every analysis view against that data. If the analysis is not useful
with real data manually entered, no amount of automation rescues it."

This script stands in for that hand entry so the analysis layer can be exercised
end to end before any video exists. The numbers are synthetic but shaped like
real ones: strike location drives smash factor and dispersion, mishits sit in a
fat tail rather than a symmetric band, and spin occasionally reads zero the way
this particular unit does.

Deliberately included so the UI has something to show in every state:
  * A club that clears the 30-shot / 3-session trend gate (7 iron) and clubs
    that do not (driver, pitching wedge).
  * Shots that fail validation, so review rows expand with a named reason.
  * An unannounced club change, so the ball-speed guard fires.
  * Shots with no spoken strike location, so the core view shows its coverage.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from datetime import date, timedelta

from .constants import STRIKE_LOCATIONS
from .db import connect, init_db
from . import store

# Smash-factor multiplier and carry penalty by strike location. Centre strikes
# are efficient; heel and toe strikes lose ball speed and add curvature.
STRIKE_EFFECT: dict[str, tuple[float, float, float]] = {
    # location: (smash multiplier, carry multiplier, spin-axis spread degrees)
    "center": (1.000, 1.000, 2.0),
    "high": (0.985, 0.985, 3.0),
    "toe": (0.955, 0.945, 6.5),
    "heel": (0.950, 0.935, 7.0),
    "low": (0.930, 0.900, 4.5),
    "combination": (0.920, 0.885, 8.0),
}

# Distribution of strikes -- weighted toward centre, with a real mishit tail.
STRIKE_WEIGHTS = [
    ("center", 0.42),
    ("toe", 0.15),
    ("heel", 0.14),
    ("low", 0.13),
    ("high", 0.09),
    ("combination", 0.07),
]

CLUB_PROFILES: dict[str, dict[str, float]] = {
    # club:  club speed, base smash, launch, spin, apex(ft), carry per mph ball
    "driver": {
        "club_speed": 101.0,
        "smash": 1.46,
        "launch": 13.2,
        "spin": 2450,
        "apex": 92,
        "carry_per_mph": 1.72,
    },
    "7 iron": {
        "club_speed": 84.0,
        "smash": 1.38,
        "launch": 18.6,
        "spin": 6800,
        "apex": 88,
        "carry_per_mph": 1.40,
    },
    "pitching wedge": {
        "club_speed": 76.0,
        "smash": 1.24,
        "launch": 26.5,
        "spin": 9200,
        "apex": 82,
        "carry_per_mph": 1.28,
    },
}

BALL_FLIGHT_BY_STRIKE = {
    "center": ["straight, penetrating", "little baby draw", "dead straight"],
    "toe": ["started right, hooked back", "low toe hook", "weak flare right"],
    "heel": ["cut across it, faded hard", "started left, sliced", "pushy fade"],
    "low": ["low and running", "knuckled out, no height", "thin, hot"],
    "high": ["ballooned", "high and short", "floated up"],
    "combination": ["low toe, big hook", "high heel, weak cut"],
}

SELF_ASSESSMENT = {
    "center": ["felt flushed", "nothing on the hands", "pured that"],
    "toe": ["felt it on the toe", "buzzy, out toward the end"],
    "heel": ["caught the heel", "felt it in the hosel"],
    "low": ["thin, felt it in my hands", "caught it low on the face"],
    "high": ["high on the face", "up toward the top groove"],
    "combination": ["all over the place", "low toe, awful"],
}

TAGS_BY_STRIKE = {
    "center": [],
    "toe": ["toe", "hook"],
    "heel": ["heel", "fade"],
    "low": ["thin"],
    "high": ["high-face"],
    "combination": ["mishit"],
}


def _pick_strike(rng: random.Random) -> str:
    roll = rng.random()
    cumulative = 0.0
    for name, weight in STRIKE_WEIGHTS:
        cumulative += weight
        if roll <= cumulative:
            return name
    return "center"


def _make_shot(rng: random.Random, club: str, *, announce: bool) -> dict:
    profile = CLUB_PROFILES[club]
    strike = _pick_strike(rng)
    smash_mult, carry_mult, axis_spread = STRIKE_EFFECT[strike]

    club_speed = round(rng.gauss(profile["club_speed"], 1.8), 1)
    smash = round(profile["smash"] * smash_mult + rng.gauss(0, 0.008), 2)
    ball_speed = round(club_speed * smash, 1)
    carry = round(ball_speed * profile["carry_per_mph"] * carry_mult + rng.gauss(0, 3.0), 1)
    roll_out = rng.uniform(2, 9) if club == "driver" else rng.uniform(1, 5)
    total = round(carry + roll_out, 1)
    launch = round(rng.gauss(profile["launch"], 1.6), 1)
    apex = round(profile["apex"] * carry_mult + rng.gauss(0, 6), 0)

    # Spin reads zero on this unit often enough that it must be represented.
    spin = 0 if rng.random() < 0.08 else round(rng.gauss(profile["spin"], 550), 0)
    spin_axis = 0 if spin == 0 else round(rng.gauss(0, axis_spread), 1)

    face_angle = round(rng.gauss(0.4, 1.8), 1)
    club_path = round(rng.gauss(-0.6, 2.0), 1)

    spoke_strike = rng.random() < 0.82
    return {
        "club": club,
        "club_source": "stated" if announce else "inherited",
        "carry": carry,
        "total": total,
        "ball_speed": ball_speed,
        "club_speed": club_speed,
        "smash_factor": smash,
        "apex": apex,
        "spin_rate": spin,
        "spin_axis": spin_axis,
        "face_angle": face_angle,
        "club_path": club_path,
        "launch_angle": launch,
        "strike_location": strike if spoke_strike else None,
        "ball_flight": rng.choice(BALL_FLIGHT_BY_STRIKE[strike]) if spoke_strike else None,
        "self_assessment": rng.choice(SELF_ASSESSMENT[strike]) if spoke_strike else None,
        "tags": TAGS_BY_STRIKE[strike] if spoke_strike else [],
    }


def _block(rng: random.Random, club: str, count: int) -> list[dict]:
    """One club block. The first shot is announced, the rest inherit."""
    return [_make_shot(rng, club, announce=(i == 0)) for i in range(count)]


def seed(conn: sqlite3.Connection, *, seed_value: int = 7) -> list[int]:
    rng = random.Random(seed_value)
    first_day = date(2026, 6, 14)
    session_ids: list[int] = []

    # Three confirmed sessions (the hand-entered set phase 1 calls for), plus a
    # fourth left unreviewed so the review UI has real work waiting in it.
    #
    # The 7 iron blocks are sized to clear the 30-shot / 3-session trend gate
    # and the driver blocks to fall short of it, so both sides of section 7's
    # rule are visible without editing any data.
    plans = [
        # (days after first, blocks, confirm, notes)
        (0, [("7 iron", 13), ("driver", 9), ("pitching wedge", 7)], True, "First logged session."),
        (11, [("7 iron", 12), ("driver", 8)], True, "Worked on strike location off the heel."),
        (25, [("7 iron", 13), ("pitching wedge", 6)], True, "Tripod, better framing."),
        (
            32,
            [("7 iron", 10), ("driver", 7)],
            False,
            "Just ingested. Not reviewed yet.",
        ),
    ]

    for offset, blocks, confirm, notes in plans:
        day = first_day + timedelta(days=offset)
        session = store.create_session(
            conn,
            source_filename=f"range-{day.isoformat()}.mp4",
            recorded_at=f"{day.isoformat()}T17:30:00",
            duration_s=3300 + rng.uniform(-300, 400),
            fps=30.0,
            resolution="1080x1920",
            status="extracted",
            notes=notes,
        )
        sid = session["id"]
        session_ids.append(sid)

        impact = 45.0
        for club, count in blocks:
            for shot in _block(rng, club, count):
                impact += rng.uniform(28, 52)
                store.create_shot(
                    conn,
                    sid,
                    impact_ts=round(impact, 2),
                    panel_change_ts=round(impact + rng.uniform(2.2, 3.1), 2),
                    **shot,
                )

        _inject_review_cases(conn, sid, rng)
        if confirm:
            # A confirmed session is one a human has already been through, so
            # the injected failures are resolved before confirming -- otherwise
            # the analysis layer would be reading numbers nobody vetted.
            _resolve_review_cases(conn, sid)
            store.confirm_session(conn, sid)

    return session_ids


def _inject_review_cases(conn: sqlite3.Connection, session_id: int, rng: random.Random) -> None:
    """Add the failure modes the review UI exists to catch."""
    shots = store.list_shots(conn, session_id)
    if len(shots) < 6:
        return

    # An OCR-style misread: smash factor no longer matches ball / club speed,
    # so section 3's first identity fails and the row is flagged.
    victim = shots[3]
    store.update_shot(conn, victim["id"], smash_factor=round(victim["smash_factor"] + 0.09, 2))

    # A club swapped mid-block without being announced. Both speeds rise the way
    # they would on a genuinely longer club, so smash factor stays plausible and
    # the only thing that gives the switch away is the ball-speed jump -- which
    # is exactly the signal the trailing-median guard watches.
    switcher = shots[len(shots) // 2]
    new_ball = round(float(switcher["ball_speed"]) + 21.0, 1)
    store.update_shot(
        conn,
        switcher["id"],
        ball_speed=new_ball,
        club_speed=round(new_ball / float(switcher["smash_factor"]), 1),
        club_source="inherited",
    )

    # A launch angle lost to the faded bottom row.
    store.update_shot(conn, shots[-2]["id"], launch_angle=None)

    store.rerun_club_change_guard(conn, session_id)


def _resolve_review_cases(conn: sqlite3.Connection, session_id: int) -> None:
    """Stand in for a human working through the review UI.

    Repairs exactly what a reviewer would: recompute a mis-OCR'd smash factor
    from ball and club speed, fill the launch angle the faded row lost, and
    accept the flagged club change. Zero-spin shots keep their flag -- the
    reviewer cannot invent a measurement the unit never made.
    """
    for shot in store.list_shots(conn, session_id):
        fixes: dict[str, float | str] = {}
        flags = shot["flags"]

        if any(f.startswith("smash_mismatch") for f in flags):
            fixes["smash_factor"] = round(
                float(shot["ball_speed"]) / float(shot["club_speed"]), 2
            )
        if any(f.startswith("missing_metric") for f in flags) and shot["launch_angle"] is None:
            fixes["launch_angle"] = 18.4
        if any(f.startswith("suspected_club_change") for f in flags):
            # The reviewer confirms the club really was the inherited one.
            fixes["club"] = shot["club"]

        if fixes:
            store.update_shot(conn, shot["id"], **fixes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed three sessions of hand-entered data.")
    parser.add_argument("--reset", action="store_true", help="delete existing sessions first")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed")
    args = parser.parse_args(argv)

    init_db()
    conn = connect()
    try:
        if args.reset:
            conn.execute("DELETE FROM sessions")
        ids = seed(conn, seed_value=args.seed)
        total = conn.execute("SELECT COUNT(*) AS n FROM shots").fetchone()["n"]
        confirmed = conn.execute(
            "SELECT COUNT(*) AS n FROM shots WHERE confidence = 'high'"
        ).fetchone()["n"]
        flagged = conn.execute(
            "SELECT COUNT(*) AS n FROM shots WHERE flags != '[]'"
        ).fetchone()["n"]
    finally:
        conn.close()

    print(f"seeded sessions {ids}")
    print(f"{total} shots, {confirmed} confirmed, {flagged} flagged for review")
    print(f"strike locations in use: {', '.join(STRIKE_LOCATIONS)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
