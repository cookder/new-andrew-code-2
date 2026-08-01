"""SQLite access. Single-user, local-first, no connection pool needed."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


#: FastAPI runs sync dependencies and sync endpoints on *different* threadpool
#: threads, so a connection opened in the dependency is used from another thread
#: in the handler. Each request still gets its own connection and only one
#: thread touches it at a time, so relaxing the check is safe here -- without
#: it, every endpoint raises "SQLite objects created in a thread can only be
#: used in that same thread".
_SAME_THREAD = False


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Read/write connection."""
    path = Path(db_path) if db_path is not None else settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return _configure(
        sqlite3.connect(path, isolation_level=None, check_same_thread=_SAME_THREAD)
    )


def connect_readonly(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Read-only connection.

    Spec section 8, phase 5: the insight agent runs with "read-only
    credentials". SQLite has no user accounts, so the equivalent is opening the
    file in ``mode=ro`` -- enforced by the driver, not by our SQL parsing.
    """
    path = Path(db_path) if db_path is not None else settings.db_path
    uri = f"file:{path}?mode=ro"
    return _configure(
        sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=_SAME_THREAD)
    )


def init_db(db_path: Path | str | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# --------------------------------------------------------------------------
# Row helpers
# --------------------------------------------------------------------------


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    if "flags" in d and isinstance(d["flags"], str):
        d["flags"] = json.loads(d["flags"])
    if "calibration_transform" in d and isinstance(d["calibration_transform"], str):
        d["calibration_transform"] = json.loads(d["calibration_transform"])
    return d


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]
