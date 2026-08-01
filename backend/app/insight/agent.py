"""Phase 5 -- the insight agent.

Spec section 8: "A read-only text-to-SQL agent over the confirmed dataset.
Natural language questions, generated query, results rendered as table or chart,
with the SQL always visible. Read-only credentials. This is the only genuinely
agentic component in the system."

Three properties are load-bearing and all three are enforced here:

1. **Read-only.** The connection is opened ``mode=ro`` (SQLite's own enforcement)
   and the generated SQL is additionally checked by :mod:`.sql_guard`.
2. **Confirmed dataset only.** The agent is pointed at the ``confirmed_shots``
   view, which filters to ``confidence = 'high'``. Unreviewed extraction output
   is not in its world.
3. **SQL always visible.** The query is returned to the caller whether it
   succeeded, failed, or was rejected by the guard. There is no path where the
   UI shows a number without the query behind it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..config import settings
from ..constants import CLUB_VOCABULARY, STRIKE_LOCATIONS, UNITS
from ..db import connect_readonly
from .sql_guard import UnsafeSQL, run_readonly

QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "description": "A single read-only SQLite SELECT (or WITH ... SELECT) query.",
        },
        "explanation": {
            "type": "string",
            "description": "One or two sentences on what the query measures and any caveat.",
        },
        "chart_hint": {
            "type": "string",
            "enum": ["table", "bar", "line", "scatter"],
            "description": "How the result is best rendered.",
        },
    },
    "required": ["sql", "explanation", "chart_hint"],
    "additionalProperties": False,
}


def schema_prompt(conn: sqlite3.Connection) -> str:
    """The DDL the model is allowed to query, read live from the database."""
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'confirmed_shots'"
    ).fetchall()
    ddl = "\n".join(r["sql"] for r in rows if r["sql"])
    return ddl


def _system_prompt(ddl: str) -> str:
    return f"""You write SQLite queries against a personal golf launch-monitor database.

You may query exactly one relation, the view `confirmed_shots`:

{ddl}

Rules:
- Output a single SELECT (or WITH ... SELECT) statement. No writes, no PRAGMA,
  no ATTACH, no multiple statements.
- `confirmed_shots` already contains only human-reviewed shots. Do not add a
  confidence filter; there is no such column.
- Column names carry their units. Distances are yards, speeds are mph, apex is
  FEET, spin is rpm, angles are degrees, smash factor is a unitless ratio.
- Prefer median over mean. Small samples with fat mishit tails make means
  misleading. SQLite has no median function, so compute it with a window
  function or an ordered subquery when a median is wanted.
- Always return the sample size (COUNT(*)) alongside any aggregate. An
  aggregate without its n is not an answer.
- `strike_location` is the highest-value column: it is spoken feedback about
  where on the face the ball was struck, and no launch monitor reports it.
  Valid values: {', '.join(STRIKE_LOCATIONS)}, or NULL when the golfer did not say.
- `spin_rate_rpm` and `spin_axis_deg` are NULL when the unit failed to measure
  them. Filter out NULLs rather than treating them as zero.
- Club names look like: {', '.join(CLUB_VOCABULARY[:8])}, etc.
- Units by metric: {json.dumps(UNITS)}

Be literal about what the user asked. If the question is ambiguous, pick the
most useful reading and say which one you picked in the explanation."""


def generate_sql(question: str, ddl: str) -> dict[str, Any]:
    """Ask Claude for a query. Raises RuntimeError if the SDK/key is missing."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RuntimeError(
            "the anthropic SDK is not installed; run `pip install -e .[llm]`"
        ) from exc

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.insight_model,
        max_tokens=16000,
        system=_system_prompt(ddl),
        messages=[{"role": "user", "content": question}],
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": QUERY_SCHEMA},
        },
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to answer this question")

    text = "".join(block.text for block in response.content if block.type == "text")
    return json.loads(text)


def answer_question(question: str, *, max_rows: int = 200) -> dict[str, Any]:
    """Full round trip: question -> SQL -> rows.

    Never raises. Every failure path still returns the SQL (when one was
    generated) so the caller can show what was attempted.
    """
    result: dict[str, Any] = {
        "question": question,
        "sql": None,
        "explanation": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "chart_hint": None,
        "error": None,
    }

    try:
        conn = connect_readonly()
    except sqlite3.Error as exc:
        result["error"] = f"could not open the database read-only: {exc}"
        return result

    try:
        ddl = schema_prompt(conn)
        try:
            plan = generate_sql(question, ddl)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI verbatim
            result["error"] = str(exc)
            return result

        result["sql"] = plan.get("sql")
        result["explanation"] = plan.get("explanation")
        result["chart_hint"] = plan.get("chart_hint")

        try:
            columns, rows, truncated = run_readonly(conn, plan["sql"], max_rows)
        except UnsafeSQL as exc:
            result["error"] = f"query rejected: {exc}"
            return result
        except sqlite3.Error as exc:
            result["error"] = f"query failed: {exc}"
            return result

        result["columns"] = columns
        result["rows"] = rows
        result["row_count"] = len(rows)
        result["truncated"] = truncated
        return result
    finally:
        conn.close()
