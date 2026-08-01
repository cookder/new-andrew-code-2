"""Stage 4 -- stat extraction.

For each confirmed shot: grab a full-resolution frame at
``panel_change_ts + 0.5s``, canonicalize the panel, send it to Claude vision
with a strict JSON schema keyed by *grid position*, derive ``face_to_path``
arithmetically, validate, and persist the source frame to disk.

Two rules from the spec shape the schema below:

* **Keyed by grid position, not by caption.** The model is asked for
  ``row1_left``, ``row1_right``, ... and the mapping from position to metric
  lives in :data:`app.constants.PANEL_GRID`. Asking the model to read the tiny
  captions would reintroduce the exact failure the spec rules out.
* **face_to_path is never taken from OCR.** The model still reports row 6 left
  when it is legible, but that reading is used only as a cross-check against
  ``face_angle - club_path`` (spec section 3's second identity). The stored
  value is the derived one -- enforced by the database, where the column is
  GENERATED.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import settings
from ..constants import (
    FLAG_LOW_CONFIDENCE_OCR,
    PANEL_GRID,
    STAT_FRAME_OFFSET_S,
    UNITS,
)
from .. import store
from . import calibration

#: The vision model answers in grid coordinates. The caption text is never used.
CELL_KEYS: tuple[str, ...] = tuple(
    f"row{r + 1}_{'left' if c == 0 else 'right'}"
    for r in range(len(PANEL_GRID))
    for c in range(2)
)

GRID_TO_METRIC: dict[str, str] = {
    f"row{r + 1}_{'left' if c == 0 else 'right'}": key
    for r, row in enumerate(PANEL_GRID)
    for c, key in enumerate(row)
}

PANEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{
            key: {
                "type": ["number", "null"],
                "description": f"Value in grid cell {key}, or null if unreadable.",
            }
            for key in CELL_KEYS
        },
        "unreadable_cells": {
            "type": "array",
            "items": {"type": "string", "enum": list(CELL_KEYS)},
            "description": "Cells you could not read with confidence.",
        },
    },
    "required": [*CELL_KEYS, "unreadable_cells"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = f"""You read a golf simulator launch-monitor stat panel from an image.

The panel is a fixed 2-column, 6-row grid. Report the NUMBER you see in each
grid cell, addressed by position: row1_left, row1_right, row2_left, and so on
down to row6_right.

Critical instructions:
- Read by grid POSITION only. Do not use the small caption text under each
  number to decide what a cell contains, and do not reorder cells to match what
  you think a metric should be. Position is the only thing that determines
  meaning downstream.
- Report the bare number. Strip any unit suffix, and keep the sign: values in
  the lower rows are frequently negative.
- Row 6 renders with a fade gradient and is often hard to read. It has been
  contrast-boosted before you see it. If a cell is still not legible, return
  null for it and list it in unreadable_cells. A guess is much worse than a null
  here.
- Never infer a value from the other cells. If a number is obscured, it is null.

For reference, this is what each position means and its unit (you do not need
to verify this, it is only to help you sanity-check magnitudes):
{json.dumps({k: f"{v} ({UNITS[v]})" for k, v in GRID_TO_METRIC.items()}, indent=2)}"""


def _encode_png(image) -> str:
    import cv2

    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("failed to encode panel image")
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def read_panel(panel_image) -> dict[str, Any]:
    """Send one canonicalized panel to Claude vision and get grid-keyed numbers."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("the anthropic SDK is not installed") from exc
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.vision_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _encode_png(panel_image),
                        },
                    },
                    {"type": "text", "text": "Read every grid cell."},
                ],
            }
        ],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": PANEL_SCHEMA},
        },
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to read this panel")
    text = "".join(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def grid_to_metrics(reading: dict[str, Any]) -> tuple[dict[str, float | None], float | None, list[str]]:
    """Translate a grid-keyed reading into metric values.

    Returns (metric values, the OCR'd face-to-path observation, flags).
    The face-to-path reading is split out deliberately: it is a verification
    input, never a stored value.
    """
    values: dict[str, float | None] = {}
    observed_face_to_path: float | None = None

    for grid_key, metric in GRID_TO_METRIC.items():
        raw = reading.get(grid_key)
        value = None if raw is None else float(raw)
        if metric == "face_to_path":
            observed_face_to_path = value
            continue  # derived; never written
        values[metric] = value

    flags: list[str] = []
    if reading.get("unreadable_cells"):
        flags.append(FLAG_LOW_CONFIDENCE_OCR)
    return values, observed_face_to_path, flags


def extract_shot_stats(
    conn: sqlite3.Connection,
    session_id: int,
    shot_id: int,
    panel_change_ts: float,
) -> dict:
    """Full stage 4 for one shot: frame -> panel -> OCR -> validate -> persist."""
    import cv2

    session = store.get_session(conn, session_id)
    if session is None:
        raise RuntimeError(f"no session {session_id}")
    transform = (session.get("calibration_transform") or {}).get("matrix")
    if not transform:
        raise RuntimeError("session is not calibrated")

    frame = calibration.grab_frame(
        session["source_video_path"], panel_change_ts + STAT_FRAME_OFFSET_S
    )
    panel = calibration.prepare_panel(frame, transform)

    reading = read_panel(panel)
    values, observed, flags = grid_to_metrics(reading)

    # Keep the source frame so this shot can be re-read later without touching
    # the (possibly deleted) source video.
    frame_dir = settings.artifact_dir / f"session-{session_id}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_path = frame_dir / f"shot-{shot_id}-panel.png"
    cv2.imwrite(str(frame_path), panel)
    store.add_artifact(conn, shot_id, "stat_frame", frame_path)

    shot = store.update_shot(conn, shot_id, **values)
    # update_shot re-validates; fold in the OCR-specific findings.
    existing = shot["flags"] if shot else []
    from ..validation import check_face_to_path

    mismatch = check_face_to_path(
        values.get("face_angle"), values.get("club_path"), observed
    )
    merged = list(dict.fromkeys([*existing, *flags, *( [mismatch] if mismatch else [] )]))
    conn.execute(
        "UPDATE shots SET flags = ? WHERE id = ?", (json.dumps(merged), shot_id)
    )
    return store.get_shot(conn, shot_id)  # type: ignore[return-value]
