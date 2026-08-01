"""CRUD over sessions, shots, tags, artifacts.

Every write path funnels through :func:`_apply_validation` so a row can never
reach the database with stale flags or an un-normalized zero spin value.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .constants import FLAG_SUSPECTED_CLUB_CHANGE, WRITABLE_METRIC_KEYS
from .db import row_to_dict, rows_to_dicts, transaction
from .validation import flag_unannounced_club_changes, normalize_metrics, validate_shot

SHOT_TEXT_FIELDS = (
    "club",
    "club_source",
    "strike_location",
    "ball_flight",
    "self_assessment",
)
SHOT_TS_FIELDS = ("impact_ts", "panel_change_ts")
SHOT_WRITABLE = (*WRITABLE_METRIC_KEYS, *SHOT_TEXT_FIELDS, *SHOT_TS_FIELDS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


def create_session(conn: sqlite3.Connection, **fields: Any) -> dict:
    cols = [
        "source_filename",
        "recorded_at",
        "duration_s",
        "fps",
        "resolution",
        "calibration_transform",
        "status",
        "source_video_path",
        "audio_path",
        "notes",
    ]
    payload = {c: fields.get(c) for c in cols if c in fields}
    if isinstance(payload.get("calibration_transform"), (dict, list)):
        payload["calibration_transform"] = json.dumps(payload["calibration_transform"])
    payload.setdefault("status", "processing")

    keys = list(payload)
    sql = (
        f"INSERT INTO sessions ({', '.join(keys)}) "
        f"VALUES ({', '.join('?' for _ in keys)}) RETURNING *"
    )
    row = conn.execute(sql, [payload[k] for k in keys]).fetchone()
    return row_to_dict(row)  # type: ignore[return-value]


def get_session(conn: sqlite3.Connection, session_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row_to_dict(row)


def list_sessions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM shots WHERE session_id = s.id) AS shot_count,
               (SELECT COUNT(*) FROM shots
                 WHERE session_id = s.id AND flags != '[]') AS flagged_count,
               (SELECT COUNT(*) FROM shots
                 WHERE session_id = s.id AND confidence = 'high') AS confirmed_count
        FROM sessions s
        ORDER BY COALESCE(s.recorded_at, s.ingested_at) DESC, s.id DESC
        """
    ).fetchall()
    return rows_to_dicts(rows)


def update_session(conn: sqlite3.Connection, session_id: int, **fields: Any) -> dict | None:
    allowed = {
        "source_filename",
        "recorded_at",
        "duration_s",
        "fps",
        "resolution",
        "calibration_transform",
        "status",
        "notes",
        "source_video_path",
        "audio_path",
        "source_deleted_at",
        "reviewed_at",
    }
    payload = {k: v for k, v in fields.items() if k in allowed}
    if not payload:
        return get_session(conn, session_id)
    if isinstance(payload.get("calibration_transform"), (dict, list)):
        payload["calibration_transform"] = json.dumps(payload["calibration_transform"])
    sets = ", ".join(f"{k} = ?" for k in payload)
    conn.execute(
        f"UPDATE sessions SET {sets} WHERE id = ?", [*payload.values(), session_id]
    )
    return get_session(conn, session_id)


def delete_session(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def confirm_session(conn: sqlite3.Connection, session_id: int) -> dict:
    """Spec section 6: confirming a session sets ``confidence = high`` on its
    shots and makes them eligible for analysis.

    This is the only place ``confidence`` is ever raised. Nothing enters the
    confirmed dataset without passing through here.
    """
    now = _now()
    with transaction(conn):
        conn.execute(
            "UPDATE shots SET confidence = 'high', reviewed_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        conn.execute(
            "UPDATE sessions SET status = 'confirmed', reviewed_at = ? WHERE id = ?",
            (now, session_id),
        )
    return get_session(conn, session_id)  # type: ignore[return-value]


def unconfirm_session(conn: sqlite3.Connection, session_id: int) -> dict:
    """Pull a session back out of the confirmed dataset."""
    with transaction(conn):
        conn.execute(
            "UPDATE shots SET confidence = 'needs_review', reviewed_at = NULL "
            "WHERE session_id = ?",
            (session_id,),
        )
        conn.execute(
            "UPDATE sessions SET status = 'extracted', reviewed_at = NULL WHERE id = ?",
            (session_id,),
        )
    return get_session(conn, session_id)  # type: ignore[return-value]


# --------------------------------------------------------------------------
# Shots
# --------------------------------------------------------------------------


def _apply_validation(
    values: dict[str, Any],
    *,
    extra_flags: list[str] | None = None,
    observed_face_to_path: float | None = None,
) -> tuple[dict[str, Any], list[str]]:
    cleaned, flags = normalize_metrics(values)
    flags = [
        *flags,
        *validate_shot(
            cleaned,
            club=values.get("club"),
            observed_face_to_path=observed_face_to_path,
        ),
        *(extra_flags or []),
    ]
    merged = dict(values)
    merged.update(cleaned)
    # dedupe, order-stable
    return merged, list(dict.fromkeys(flags))


def _next_shot_index(conn: sqlite3.Connection, session_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(shot_index), 0) + 1 AS n FROM shots WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row["n"])


def create_shot(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    shot_index: int | None = None,
    tags: list[str] | None = None,
    observed_face_to_path: float | None = None,
    extra_flags: list[str] | None = None,
    **fields: Any,
) -> dict:
    values = {k: fields.get(k) for k in SHOT_WRITABLE}
    if values.get("club") and not values.get("club_source"):
        values["club_source"] = "manual"
    values, flags = _apply_validation(
        values, extra_flags=extra_flags, observed_face_to_path=observed_face_to_path
    )

    idx = shot_index if shot_index is not None else _next_shot_index(conn, session_id)
    keys = [k for k in SHOT_WRITABLE]
    sql = (
        "INSERT INTO shots (session_id, shot_index, flags, "
        + ", ".join(keys)
        + ") VALUES (?, ?, ?, "
        + ", ".join("?" for _ in keys)
        + ") RETURNING id"
    )
    params = [session_id, idx, json.dumps(flags), *[values.get(k) for k in keys]]
    shot_id = int(conn.execute(sql, params).fetchone()["id"])

    if tags:
        set_tags(conn, shot_id, tags)
    return get_shot(conn, shot_id)  # type: ignore[return-value]


def get_shot(conn: sqlite3.Connection, shot_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
    shot = row_to_dict(row)
    if shot is None:
        return None
    shot["tags"] = get_tags(conn, shot_id)
    shot["artifacts"] = get_artifacts(conn, shot_id)
    return shot


def list_shots(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM shots WHERE session_id = ? ORDER BY shot_index", (session_id,)
    ).fetchall()
    shots = rows_to_dicts(rows)
    for shot in shots:
        shot["tags"] = get_tags(conn, shot["id"])
        shot["artifacts"] = get_artifacts(conn, shot["id"])
    return shots


def update_shot(
    conn: sqlite3.Connection,
    shot_id: int,
    *,
    tags: list[str] | None = None,
    **fields: Any,
) -> dict | None:
    current = get_shot(conn, shot_id)
    if current is None:
        return None

    merged = {k: current.get(k) for k in SHOT_WRITABLE}
    touched = {k: v for k, v in fields.items() if k in SHOT_WRITABLE}
    merged.update(touched)

    # An inline edit to the club in the review UI is a human decision.
    if "club" in touched and "club_source" not in touched:
        merged["club_source"] = "manual"

    # A human editing a value has resolved whatever the automation suspected,
    # so a stale suspected-club-change flag should not survive the edit.
    keep = [f for f in current.get("flags", []) if not f.startswith(FLAG_SUSPECTED_CLUB_CHANGE)]
    preserved = keep if "club" in touched else current.get("flags", [])
    carried = [f for f in preserved if f == FLAG_SUSPECTED_CLUB_CHANGE]

    merged, flags = _apply_validation(merged, extra_flags=carried)

    sets = ", ".join(f"{k} = ?" for k in SHOT_WRITABLE)
    conn.execute(
        f"UPDATE shots SET {sets}, flags = ? WHERE id = ?",
        [*[merged.get(k) for k in SHOT_WRITABLE], json.dumps(flags), shot_id],
    )
    if tags is not None:
        set_tags(conn, shot_id, tags)
    return get_shot(conn, shot_id)


def delete_shot(conn: sqlite3.Connection, shot_id: int) -> None:
    conn.execute("DELETE FROM shots WHERE id = ?", (shot_id,))


def bulk_set_club(
    conn: sqlite3.Connection,
    session_id: int,
    club: str,
    *,
    from_index: int,
    to_index: int,
) -> int:
    """Spec section 6: bulk club reassignment for a contiguous range of shots.

    This is the one action that repairs an unannounced club switch across a
    whole block, so it also clears the suspected-club-change flag it was
    prompted by.
    """
    lo, hi = min(from_index, to_index), max(from_index, to_index)
    rows = conn.execute(
        "SELECT id, flags FROM shots WHERE session_id = ? AND shot_index BETWEEN ? AND ?",
        (session_id, lo, hi),
    ).fetchall()
    with transaction(conn):
        for row in rows:
            flags = [
                f
                for f in json.loads(row["flags"])
                if not f.startswith(FLAG_SUSPECTED_CLUB_CHANGE)
            ]
            conn.execute(
                "UPDATE shots SET club = ?, club_source = 'manual', flags = ? WHERE id = ?",
                (club, json.dumps(flags), row["id"]),
            )
    return len(rows)


def rerun_club_change_guard(conn: sqlite3.Connection, session_id: int) -> None:
    """Recompute the unannounced-club-change flag across a session in order."""
    shots = list_shots(conn, session_id)
    extras = flag_unannounced_club_changes(shots)
    with transaction(conn):
        for shot, extra in zip(shots, extras, strict=True):
            flags = [f for f in shot["flags"] if f != FLAG_SUSPECTED_CLUB_CHANGE]
            flags.extend(extra)
            conn.execute(
                "UPDATE shots SET flags = ? WHERE id = ?",
                (json.dumps(list(dict.fromkeys(flags))), shot["id"]),
            )


# --------------------------------------------------------------------------
# Tags & artifacts
# --------------------------------------------------------------------------


def get_tags(conn: sqlite3.Connection, shot_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT tag FROM shot_tags WHERE shot_id = ? ORDER BY tag", (shot_id,)
    ).fetchall()
    return [r["tag"] for r in rows]


def set_tags(conn: sqlite3.Connection, shot_id: int, tags: list[str]) -> list[str]:
    clean = sorted({t.strip().lower() for t in tags if t and t.strip()})
    with transaction(conn):
        conn.execute("DELETE FROM shot_tags WHERE shot_id = ?", (shot_id,))
        conn.executemany(
            "INSERT INTO shot_tags (shot_id, tag) VALUES (?, ?)",
            [(shot_id, t) for t in clean],
        )
    return clean


def add_artifact(conn: sqlite3.Connection, shot_id: int, kind: str, path: str) -> dict:
    row = conn.execute(
        "INSERT INTO artifacts (shot_id, kind, path) VALUES (?, ?, ?) "
        "ON CONFLICT (shot_id, kind) DO UPDATE SET path = excluded.path RETURNING *",
        (shot_id, kind, str(path)),
    ).fetchone()
    return row_to_dict(row)  # type: ignore[return-value]


def get_artifacts(conn: sqlite3.Connection, shot_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM artifacts WHERE shot_id = ? ORDER BY kind", (shot_id,)
    ).fetchall()
    return rows_to_dicts(rows)


# --------------------------------------------------------------------------
# Anomalies
# --------------------------------------------------------------------------


def add_anomaly(
    conn: sqlite3.Connection, session_id: int, kind: str, ts: float | None, detail: str = ""
) -> None:
    conn.execute(
        "INSERT INTO anomalies (session_id, kind, ts, detail) VALUES (?, ?, ?, ?)",
        (session_id, kind, ts, detail),
    )


def list_anomalies(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM anomalies WHERE session_id = ? ORDER BY ts", (session_id,)
    ).fetchall()
    return rows_to_dicts(rows)
