"""Stage 6 -- voice attribution.

For each shot, take the transcript window from ``impact_ts - 15s`` to
``impact_ts + 20s`` and ask an LLM for a structured reading of what the golfer
said. The asymmetric window is deliberate: club selection is stated *before* the
swing and feedback comes *after* it.

    "Strike location is voice-only. The simulator does not report it. It is the
    highest-value field in the system and the primary reason the audio pipeline
    exists."

Two rules govern club assignment:

* **Persistence.** If no club is stated, inherit the previous shot's club.
* **Unannounced-change guard.** Persistence will silently mislabel an entire
  block if the golfer switches clubs without saying so. If a shot's ball speed
  deviates more than 12 mph from the trailing 3-shot median for the inherited
  club, flag ``suspected_club_change``. Flag only -- never auto-correct.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..config import settings
from ..constants import (
    CLUB_VOCABULARY,
    FLAG_NO_CLUB,
    FLAG_SUSPECTED_CLUB_CHANGE,
    STRIKE_LOCATIONS,
    VOICE_WINDOW_AFTER_S,
    VOICE_WINDOW_BEFORE_S,
)
from ..validation import suspected_club_change
from .. import store
from .transcription import load_transcript, window

VOICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "club_stated": {
            "type": ["string", "null"],
            "description": (
                "The club the golfer named for THIS shot, normalized (e.g. '7 iron', "
                "'driver', '4 hybrid', 'pitching wedge'). Null if no club was named."
            ),
        },
        "strike_location": {
            "type": ["string", "null"],
            "enum": [*STRIKE_LOCATIONS, None],
            "description": "Where on the clubface the golfer said the ball was struck.",
        },
        "ball_flight": {
            "type": ["string", "null"],
            "description": "What the golfer said about the flight, in their own words.",
        },
        "self_assessment": {
            "type": ["string", "null"],
            "description": "What the golfer said about how the swing felt.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short lowercase descriptors, e.g. thin, pull, chunked.",
        },
    },
    "required": [
        "club_stated",
        "strike_location",
        "ball_flight",
        "self_assessment",
        "tags",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = f"""You read a short transcript window from a golf range session and extract
what the golfer said about one shot.

The window covers roughly 15 seconds before impact and 20 seconds after it. The
golfer typically names a club before swinging and comments on the strike, the
ball flight, and how it felt afterwards.

Rules:
- Only report what was actually said. Never infer a club, a strike location, or
  a ball flight from context or from what would be typical. Null is the correct
  answer when the golfer did not say.
- club_stated: only fill this if a club is named for THIS shot. If the golfer
  says something like "same club" or "one more", that is not naming a club --
  return null and the club will be inherited. Normalize to forms like:
  {', '.join(CLUB_VOCABULARY[:12])}. Beware two known transcription errors:
  "for hybrid" is "4 hybrid", and nine/five iron are frequently confused --
  prefer the reading that the surrounding words support.
- strike_location: map the golfer's words onto one of
  {', '.join(STRIKE_LOCATIONS)}. "Off the heel" -> heel. "Toe-y" -> toe.
  "Flushed"/"middle"/"pured" -> center. "Thin"/"low on the face" -> low.
  "Caught it high" -> high. Use "combination" only when the golfer names two
  locations at once (e.g. "low toe"). This field is the single most valuable
  output; be accurate and return null rather than guessing.
- ball_flight and self_assessment: quote or lightly paraphrase the golfer. Keep
  them short.
- tags: 0-4 short lowercase words for filtering later."""


def attribute_window(text: str) -> dict[str, Any]:
    """One LLM call over one shot's transcript window."""
    if not text.strip():
        return {
            "club_stated": None,
            "strike_location": None,
            "ball_flight": None,
            "self_assessment": None,
            "tags": [],
        }

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("the anthropic SDK is not installed") from exc
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.voice_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": VOICE_SCHEMA},
        },
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to read this transcript window")
    body = "".join(b.text for b in response.content if b.type == "text")
    return json.loads(body)


def resolve_clubs(attributions: list[dict[str, Any]], ball_speeds: list[float | None]) -> list[dict]:
    """Apply club persistence and the unannounced-change guard across a session.

    Pure function over already-extracted attributions, so the whole rule is
    testable without touching audio or the network.

    The trailing window resets on every *stated* club, because an announcement
    re-establishes ground truth and speeds from before it belong to a different
    club.
    """
    resolved: list[dict] = []
    current: str | None = None
    trailing: list[float] = []

    for attribution, ball_speed in zip(attributions, ball_speeds, strict=True):
        stated = attribution.get("club_stated")
        flags: list[str] = []

        if stated:
            current = stated
            source = "stated"
            trailing = []
        elif current is not None:
            source = "inherited"
            if suspected_club_change(ball_speed, trailing):
                flags.append(FLAG_SUSPECTED_CLUB_CHANGE)
        else:
            source = None
            flags.append(FLAG_NO_CLUB)

        if ball_speed is not None:
            trailing.append(float(ball_speed))

        resolved.append(
            {
                **attribution,
                "club": current,
                "club_source": source,
                "flags": flags,
            }
        )
    return resolved


def attribute_session(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    """Run stage 6 over every shot in a session and write the results."""
    transcript = load_transcript(conn, session_id)
    if transcript is None:
        raise RuntimeError(f"session {session_id} has no transcript; run stage 5 first")

    shots = store.list_shots(conn, session_id)
    attributions = []
    for shot in shots:
        impact = shot.get("impact_ts")
        if impact is None:
            attributions.append(attribute_window(""))
            continue
        text = window(
            transcript["words"],
            impact - VOICE_WINDOW_BEFORE_S,
            impact + VOICE_WINDOW_AFTER_S,
        )
        attributions.append(attribute_window(text))

    resolved = resolve_clubs(attributions, [s.get("ball_speed") for s in shots])

    out = []
    for shot, entry in zip(shots, resolved, strict=True):
        updated = store.update_shot(
            conn,
            shot["id"],
            club=entry["club"],
            club_source=entry["club_source"],
            strike_location=entry.get("strike_location"),
            ball_flight=entry.get("ball_flight"),
            self_assessment=entry.get("self_assessment"),
            tags=entry.get("tags") or [],
        )
        if entry["flags"] and updated is not None:
            merged = list(dict.fromkeys([*updated["flags"], *entry["flags"]]))
            conn.execute(
                "UPDATE shots SET flags = ? WHERE id = ?",
                (json.dumps(merged), shot["id"]),
            )
        out.append(store.get_shot(conn, shot["id"]))
    return out  # type: ignore[return-value]
