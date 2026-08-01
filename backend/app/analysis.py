"""Spec section 7 -- the analysis layer.

Read rules that hold everywhere in this module:

* Only ``confidence = 'high'`` shots are ever aggregated. Nothing enters the
  confirmed dataset unreviewed (section 6), and analysis reads only that set.
* Median and IQR, never mean. Small samples with fat mishit tails make means
  misleading (section 7).
* Sample size travels with every aggregate. A median of four shots and a median
  of forty are not the same claim, and the UI must be able to say so.
* Nothing here is persisted. All aggregation happens at query time from raw
  per-shot rows (section 5).
"""

from __future__ import annotations

import sqlite3
from statistics import StatisticsError, median as _median, quantiles
from typing import Any

from .constants import (
    CLUB_DISPLAY_ORDER,
    METRIC_KEYS,
    METRICS_BY_KEY,
    STRIKE_LOCATIONS,
    TREND_MIN_SESSIONS,
    TREND_MIN_SHOTS,
    UNITS,
)
from .db import rows_to_dicts

CONFIRMED = "confidence = 'high'"


# --------------------------------------------------------------------------
# Summary statistics
# --------------------------------------------------------------------------


def summarize(values: list[float | None]) -> dict[str, Any]:
    """Median + IQR + n for one metric over one group.

    ``n`` counts values actually present, which is not the same as the group's
    shot count -- spin reads zero (i.e. missing) on this unit often enough that
    conflating the two would overstate the sample behind a spin median.
    """
    clean = [float(v) for v in values if v is not None]
    n = len(clean)
    if n == 0:
        return {"n": 0, "median": None, "q1": None, "q3": None, "iqr": None,
                "min": None, "max": None}
    med = _median(clean)
    if n == 1:
        q1 = q3 = clean[0]
    else:
        try:
            # 'inclusive' treats the data as the whole population, which is
            # what we have -- these are all the shots, not a sample of them.
            q1, _, q3 = quantiles(clean, n=4, method="inclusive")
        except StatisticsError:  # pragma: no cover - guarded by n check above
            q1 = q3 = med
    return {
        "n": n,
        "median": round(med, 3),
        "q1": round(q1, 3),
        "q3": round(q3, 3),
        "iqr": round(q3 - q1, 3),
        "min": round(min(clean), 3),
        "max": round(max(clean), 3),
    }


def _metric_summaries(rows: list[dict]) -> dict[str, dict]:
    return {key: summarize([r.get(key) for r in rows]) for key in METRIC_KEYS}


def _club_sort_key(club: str | None) -> tuple[int, str]:
    if club is None:
        return (10_000, "")
    return (CLUB_DISPLAY_ORDER.get(club.lower(), 9_999), club)


def _confirmed_rows(conn: sqlite3.Connection, where: str = "", params: tuple = ()) -> list[dict]:
    sql = (
        "SELECT s.*, ses.recorded_at, date(ses.recorded_at) AS session_date "
        "FROM shots s JOIN sessions ses ON ses.id = s.session_id "
        f"WHERE s.{CONFIRMED}"
    )
    if where:
        sql += f" AND {where}"
    sql += " ORDER BY ses.recorded_at, s.shot_index"
    return rows_to_dicts(conn.execute(sql, params).fetchall())


# --------------------------------------------------------------------------
# View 1 -- per club
# --------------------------------------------------------------------------


def per_club(conn: sqlite3.Connection) -> dict[str, Any]:
    """Shot count, plus median and IQR for every metric, for each club."""
    rows = _confirmed_rows(conn)
    by_club: dict[str | None, list[dict]] = {}
    for row in rows:
        by_club.setdefault(row["club"], []).append(row)

    clubs = []
    for club in sorted(by_club, key=_club_sort_key):
        group = by_club[club]
        clubs.append(
            {
                "club": club,
                "shot_count": len(group),
                "session_count": len({r["session_id"] for r in group}),
                "metrics": _metric_summaries(group),
            }
        )
    return {"units": UNITS, "clubs": clubs}


# --------------------------------------------------------------------------
# View 2 -- strike location vs outcome (the core view)
# --------------------------------------------------------------------------


def strike_vs_outcome(conn: sqlite3.Connection, club: str | None = None) -> dict[str, Any]:
    """For each club, group by strike location and compare outcomes.

    Section 7 calls this "the core view. It is what the whole system exists to
    produce." Strike location is voice-only -- the simulator does not report it
    -- so this join between spoken feedback and launch monitor numbers is the
    thing no off-the-shelf tool can do.

    "Dispersion" here is reported two ways, because the Full Swing panel gives
    no lateral offset: carry IQR (distance consistency) and spin axis IQR
    (curvature spread, the closest available proxy for left/right scatter).
    """
    where, params = ("s.club = ?", (club,)) if club else ("", ())
    rows = _confirmed_rows(conn, where, params)

    by_club: dict[str | None, list[dict]] = {}
    for row in rows:
        by_club.setdefault(row["club"], []).append(row)

    out = []
    for club_name in sorted(by_club, key=_club_sort_key):
        group = by_club[club_name]
        buckets: dict[str | None, list[dict]] = {}
        for row in group:
            buckets.setdefault(row["strike_location"], []).append(row)

        # Stable ordering: known locations in spec order, then unattributed.
        ordered = [loc for loc in STRIKE_LOCATIONS if loc in buckets]
        if None in buckets:
            ordered.append(None)  # type: ignore[arg-type]

        locations = []
        for loc in ordered:
            bucket = buckets[loc]
            carry = summarize([r.get("carry") for r in bucket])
            spin_axis = summarize([r.get("spin_axis") for r in bucket])
            locations.append(
                {
                    "strike_location": loc,
                    "shot_count": len(bucket),
                    "smash_factor": summarize([r.get("smash_factor") for r in bucket]),
                    "carry": carry,
                    "ball_speed": summarize([r.get("ball_speed") for r in bucket]),
                    "apex": summarize([r.get("apex") for r in bucket]),
                    "dispersion": {
                        "carry_iqr": carry["iqr"],
                        "spin_axis_iqr": spin_axis["iqr"],
                        "carry_range": (
                            None
                            if carry["max"] is None
                            else round(carry["max"] - carry["min"], 3)
                        ),
                    },
                }
            )

        attributed = sum(len(b) for loc, b in buckets.items() if loc is not None)
        out.append(
            {
                "club": club_name,
                "shot_count": len(group),
                "attributed_count": attributed,
                "locations": locations,
            }
        )
    return {"units": UNITS, "clubs": out}


# --------------------------------------------------------------------------
# View 3 -- session over session
# --------------------------------------------------------------------------


def session_trend(conn: sqlite3.Connection, metric: str = "carry") -> dict[str, Any]:
    """Metric trend by club across sessions, with per-session shot counts.

    Section 7: "Do not surface a trend line until a club has at least 30
    confirmed shots across at least 3 sessions." The gate is reported per club
    as ``trend_eligible`` plus the counts behind the decision -- the API never
    hides the points, it tells the UI whether it has earned a line through
    them. Per-session shot counts ride along so single-session noise is
    obvious.
    """
    if metric not in METRICS_BY_KEY:
        raise ValueError(f"unknown metric: {metric}")

    rows = _confirmed_rows(conn)
    by_club: dict[str | None, list[dict]] = {}
    for row in rows:
        by_club.setdefault(row["club"], []).append(row)

    clubs = []
    for club in sorted(by_club, key=_club_sort_key):
        group = by_club[club]
        by_session: dict[int, list[dict]] = {}
        for row in group:
            by_session.setdefault(row["session_id"], []).append(row)

        points = []
        for session_id, bucket in sorted(
            by_session.items(), key=lambda kv: (kv[1][0]["recorded_at"] or "", kv[0])
        ):
            summary = summarize([r.get(metric) for r in bucket])
            points.append(
                {
                    "session_id": session_id,
                    "recorded_at": bucket[0]["recorded_at"],
                    "session_date": bucket[0]["session_date"],
                    "shot_count": len(bucket),
                    **summary,
                }
            )

        total = len(group)
        sessions = len(by_session)
        eligible = total >= TREND_MIN_SHOTS and sessions >= TREND_MIN_SESSIONS
        clubs.append(
            {
                "club": club,
                "shot_count": total,
                "session_count": sessions,
                "trend_eligible": eligible,
                "trend_gate": {
                    "min_shots": TREND_MIN_SHOTS,
                    "min_sessions": TREND_MIN_SESSIONS,
                    "shots_short_by": max(0, TREND_MIN_SHOTS - total),
                    "sessions_short_by": max(0, TREND_MIN_SESSIONS - sessions),
                },
                "points": points,
            }
        )

    return {
        "metric": metric,
        "unit": UNITS[metric],
        "label": METRICS_BY_KEY[metric].label,
        "clubs": clubs,
    }


# --------------------------------------------------------------------------
# View 4 -- dispersion
# --------------------------------------------------------------------------


def dispersion(conn: sqlite3.Connection, club: str | None = None) -> dict[str, Any]:
    """Face angle against club path, coloured by strike location.

    Face-to-path is the diagonal of this plot and is the derived column, so a
    point's distance from the y = x line is the club-face relationship that
    actually shapes the ball.
    """
    where, params = ("s.club = ?", (club,)) if club else ("", ())
    rows = _confirmed_rows(conn, where, params)
    points = [
        {
            "shot_id": r["id"],
            "session_id": r["session_id"],
            "club": r["club"],
            "face_angle": r["face_angle"],
            "club_path": r["club_path"],
            "face_to_path": r["face_to_path"],
            "strike_location": r["strike_location"],
            "carry": r["carry"],
            "smash_factor": r["smash_factor"],
        }
        for r in rows
        if r["face_angle"] is not None and r["club_path"] is not None
    ]
    return {
        "units": {k: UNITS[k] for k in ("face_angle", "club_path", "face_to_path")},
        "clubs": sorted({p["club"] for p in points if p["club"]}, key=_club_sort_key),
        "point_count": len(points),
        "points": points,
    }


# --------------------------------------------------------------------------
# View 5 -- shot browser
# --------------------------------------------------------------------------

_NUMERIC_FILTERS = {f"{k}_{bound}": (k, bound) for k in METRIC_KEYS for bound in ("min", "max")}


def shot_browser(
    conn: sqlite3.Connection,
    *,
    club: str | None = None,
    strike_location: str | None = None,
    session_id: int | None = None,
    tag: str | None = None,
    flagged: bool | None = None,
    confirmed_only: bool = True,
    date_from: str | None = None,
    date_to: str | None = None,
    text: str | None = None,
    limit: int = 500,
    offset: int = 0,
    **numeric: float | None,
) -> dict[str, Any]:
    """Filter by any field; every row carries its clip and frame artifacts so
    the UI is one click from watching the swing that produced the numbers."""
    clauses: list[str] = []
    params: list[Any] = []

    if confirmed_only:
        clauses.append(f"s.{CONFIRMED}")
    if club:
        clauses.append("s.club = ?")
        params.append(club)
    if strike_location:
        clauses.append("s.strike_location = ?")
        params.append(strike_location)
    if session_id is not None:
        clauses.append("s.session_id = ?")
        params.append(session_id)
    if flagged is True:
        clauses.append("s.flags != '[]'")
    elif flagged is False:
        clauses.append("s.flags = '[]'")
    if date_from:
        clauses.append("date(ses.recorded_at) >= date(?)")
        params.append(date_from)
    if date_to:
        clauses.append("date(ses.recorded_at) <= date(?)")
        params.append(date_to)
    if tag:
        clauses.append("EXISTS (SELECT 1 FROM shot_tags t WHERE t.shot_id = s.id AND t.tag = ?)")
        params.append(tag.lower())
    if text:
        clauses.append("(s.ball_flight LIKE ? OR s.self_assessment LIKE ?)")
        params.extend([f"%{text}%", f"%{text}%"])

    for name, value in numeric.items():
        if value is None or name not in _NUMERIC_FILTERS:
            continue
        key, bound = _NUMERIC_FILTERS[name]
        clauses.append(f"s.{key} {'>=' if bound == 'min' else '<='} ?")
        params.append(value)

    where = " AND ".join(clauses) if clauses else "1=1"
    base = (
        "FROM shots s JOIN sessions ses ON ses.id = s.session_id "
        f"WHERE {where}"
    )
    total = int(conn.execute(f"SELECT COUNT(*) AS n {base}", params).fetchone()["n"])
    rows = rows_to_dicts(
        conn.execute(
            "SELECT s.*, ses.recorded_at, ses.source_filename "
            f"{base} ORDER BY ses.recorded_at DESC, s.shot_index "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    )

    for row in rows:
        arts = conn.execute(
            "SELECT kind, path FROM artifacts WHERE shot_id = ?", (row["id"],)
        ).fetchall()
        row["artifacts"] = {a["kind"]: a["path"] for a in arts}
        tags = conn.execute(
            "SELECT tag FROM shot_tags WHERE shot_id = ? ORDER BY tag", (row["id"],)
        ).fetchall()
        row["tags"] = [t["tag"] for t in tags]

    return {"total": total, "limit": limit, "offset": offset, "shots": rows}


# --------------------------------------------------------------------------
# Filter vocabulary for the UI
# --------------------------------------------------------------------------


def facets(conn: sqlite3.Connection) -> dict[str, Any]:
    clubs = [
        r["club"]
        for r in conn.execute(
            "SELECT DISTINCT club FROM shots WHERE club IS NOT NULL"
        ).fetchall()
    ]
    tags = [r["tag"] for r in conn.execute("SELECT DISTINCT tag FROM shot_tags").fetchall()]
    return {
        "clubs": sorted(clubs, key=_club_sort_key),
        "tags": sorted(tags),
        "strike_locations": list(STRIKE_LOCATIONS),
    }
