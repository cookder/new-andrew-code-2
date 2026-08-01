"""Stage 5 -- transcription.

faster-whisper, word-level timestamps, over the full session audio.

Spec: "Pass a club vocabulary hint in the prompt to reduce mishears. 'Nine iron'
vs 'five iron' and 'four hybrid' vs 'for hybrid' are the known failure modes."
The first is an acoustic confusion, the second a homophone -- the vocabulary
prompt biases decoding toward the golf reading of both.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import settings
from ..constants import CLUB_VOCABULARY

#: Whisper's initial_prompt biases the decoder toward this vocabulary.
VOCAB_PROMPT = (
    "Golf range session. Club names spoken before each shot and feedback after. "
    "Clubs: " + ", ".join(CLUB_VOCABULARY) + ". "
    "Feedback words: heel, toe, center, high on the face, low on the face, thin, "
    "fat, chunked, pulled, pushed, drawn, faded, hooked, sliced, flushed, "
    "striped, blocked, pured."
)


def transcribe(audio_path: Path | str, *, model_size: str | None = None) -> dict[str, Any]:
    """Word-level transcript of the whole session."""
    from faster_whisper import WhisperModel  # lazy: phase 2 only

    model = WhisperModel(model_size or settings.whisper_model, compute_type="int8")
    segments, _ = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        initial_prompt=VOCAB_PROMPT,
        vad_filter=True,
    )

    words: list[dict[str, Any]] = []
    parts: list[str] = []
    for segment in segments:
        parts.append(segment.text)
        for word in segment.words or []:
            words.append(
                {
                    "word": word.word.strip(),
                    "start": round(word.start, 3),
                    "end": round(word.end, 3),
                }
            )
    return {
        "words": words,
        "text": " ".join(p.strip() for p in parts).strip(),
        "model": model_size or settings.whisper_model,
    }


def save_transcript(
    conn: sqlite3.Connection, session_id: int, transcript: dict[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO transcripts (session_id, words, text, model) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (session_id) DO UPDATE SET "
        "words = excluded.words, text = excluded.text, model = excluded.model",
        (
            session_id,
            json.dumps(transcript["words"]),
            transcript["text"],
            transcript.get("model"),
        ),
    )


def load_transcript(conn: sqlite3.Connection, session_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM transcripts WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    return {"words": json.loads(row["words"]), "text": row["text"], "model": row["model"]}


def window(words: list[dict[str, Any]], start: float, end: float) -> str:
    """Transcript text spoken within [start, end] seconds."""
    return " ".join(w["word"] for w in words if start <= w["start"] <= end).strip()
