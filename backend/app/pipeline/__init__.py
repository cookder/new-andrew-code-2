"""Deterministic ingest pipeline.

Spec section 4: "Deterministic stages. Two LLM calls per shot. One agent, at the
end only. Do not build a multi-agent orchestration layer for this."

Stages run in order and each is independently re-runnable from persisted
artifacts. Heavy dependencies (ffmpeg, opencv, faster-whisper, the anthropic
SDK) are imported lazily inside the functions that need them, so the API,
review UI, analysis layer, and test suite all run without them installed.
"""
