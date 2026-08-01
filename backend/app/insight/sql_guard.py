"""Belt-and-braces guard on model-generated SQL.

The real enforcement is the connection: :func:`app.db.connect_readonly` opens
the database with ``mode=ro``, so SQLite itself rejects any write regardless of
what gets past this module. This layer exists to fail *early and legibly* --
"that query was rejected because it used PRAGMA" beats an opaque driver error
-- and to stop stacked statements before they reach the driver at all.
"""

from __future__ import annotations

import re
import sqlite3

#: Statement keywords that must never appear. ATTACH would reach a second
#: database file (potentially a writable one), PRAGMA can change driver
#: behaviour, and load_extension escapes the sandbox entirely.
FORBIDDEN = (
    "attach",
    "detach",
    "pragma",
    "insert",
    "update",
    "delete",
    "drop",
    "create",
    "alter",
    "replace",
    "truncate",
    "vacuum",
    "reindex",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "release",
    "grant",
    "revoke",
    "load_extension",
    "writefile",
    "readfile",
    "edit",
)

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


class UnsafeSQL(ValueError):
    """Raised when generated SQL is not a plain read."""


def strip_noise(sql: str) -> str:
    """Remove comments and string literals so keyword checks cannot be fooled
    by a forbidden word appearing inside a quoted string or a comment."""
    out = _COMMENT_BLOCK.sub(" ", sql)
    out = _COMMENT_LINE.sub(" ", out)
    out = _STRING_LITERAL.sub("''", out)
    return out


def ensure_readonly(sql: str) -> str:
    """Validate and return a normalized single read-only statement."""
    if not sql or not sql.strip():
        raise UnsafeSQL("empty query")

    bare = strip_noise(sql).strip().rstrip(";").strip()
    if not bare:
        raise UnsafeSQL("query contains no statement")

    if ";" in bare:
        raise UnsafeSQL("only a single statement is allowed")

    lowered = bare.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise UnsafeSQL("query must start with SELECT or WITH")

    for word in FORBIDDEN:
        if re.search(rf"(?<![a-z0-9_]){re.escape(word)}(?![a-z0-9_])", lowered):
            raise UnsafeSQL(f"query uses forbidden keyword: {word.upper()}")

    return sql.strip().rstrip(";").strip()


def apply_limit(sql: str, max_rows: int) -> str:
    """Cap result size without editing the model's SQL.

    Wrapping keeps the query the user is shown byte-identical to the query that
    ran, minus the outer bound -- rewriting the inner text would make the
    "SQL always visible" promise a lie.
    """
    return f"SELECT * FROM (\n{sql}\n) LIMIT {int(max_rows) + 1}"


def run_readonly(
    conn: sqlite3.Connection, sql: str, max_rows: int = 200
) -> tuple[list[str], list[list], bool]:
    """Execute guarded SQL on a read-only connection.

    Returns (columns, rows, truncated).
    """
    safe = ensure_readonly(sql)
    cur = conn.execute(apply_limit(safe, max_rows))
    fetched = cur.fetchall()
    columns = [d[0] for d in cur.description] if cur.description else []
    truncated = len(fetched) > max_rows
    rows = [list(r) for r in fetched[:max_rows]]
    return columns, rows, truncated
