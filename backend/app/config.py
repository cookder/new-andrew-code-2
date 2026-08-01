"""Runtime configuration. Everything is overridable by environment variable so
the test suite can point at a scratch directory."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _path_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: _path_env("GOLF_DATA_DIR", Path.home() / ".golf-analyzer"))

    @property
    def db_path(self) -> Path:
        return _path_env("GOLF_DB_PATH", self.data_dir / "golf.db")

    @property
    def watch_dir(self) -> Path:
        """Stage 1 watched folder: drop an MP4 here and ingest picks it up."""
        return _path_env("GOLF_WATCH_DIR", self.data_dir / "inbox")

    @property
    def media_dir(self) -> Path:
        """Ingested source videos and extracted audio."""
        return _path_env("GOLF_MEDIA_DIR", self.data_dir / "media")

    @property
    def artifact_dir(self) -> Path:
        """Stat frames, clips, transcript segments."""
        return _path_env("GOLF_ARTIFACT_DIR", self.data_dir / "artifacts")

    @property
    def anthropic_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def vision_model(self) -> str:
        return os.environ.get("GOLF_VISION_MODEL", "claude-opus-5")

    @property
    def voice_model(self) -> str:
        return os.environ.get("GOLF_VOICE_MODEL", "claude-opus-5")

    @property
    def insight_model(self) -> str:
        return os.environ.get("GOLF_INSIGHT_MODEL", "claude-opus-5")

    @property
    def whisper_model(self) -> str:
        return os.environ.get("GOLF_WHISPER_MODEL", "medium.en")

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.watch_dir, self.media_dir, self.artifact_dir):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
