"""Request/response models. Field names mirror the SQLite columns exactly."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .constants import CLUB_SOURCES, SESSION_STATUSES, STRIKE_LOCATIONS

StrikeLocationT = Literal["heel", "toe", "center", "high", "low", "combination"]
ClubSourceT = Literal["stated", "inherited", "manual"]
SessionStatusT = Literal["processing", "needs_calibration", "extracted", "confirmed", "failed"]

assert set(STRIKE_LOCATIONS) == set(StrikeLocationT.__args__)  # type: ignore[attr-defined]
assert set(CLUB_SOURCES) == set(ClubSourceT.__args__)  # type: ignore[attr-defined]
assert set(SESSION_STATUSES) == set(SessionStatusT.__args__)  # type: ignore[attr-defined]


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_filename: str | None = None
    recorded_at: str | None = None
    duration_s: float | None = None
    fps: float | None = None
    resolution: str | None = None
    status: SessionStatusT = "processing"
    notes: str | None = None


class SessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_filename: str | None = None
    recorded_at: str | None = None
    duration_s: float | None = None
    fps: float | None = None
    resolution: str | None = None
    status: SessionStatusT | None = None
    notes: str | None = None


class ShotBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impact_ts: float | None = None
    panel_change_ts: float | None = None

    club: str | None = None
    club_source: ClubSourceT | None = None

    # face_to_path is intentionally absent: it is a generated column derived
    # from face_angle - club_path and cannot be set from outside.
    carry: float | None = None
    total: float | None = None
    ball_speed: float | None = None
    club_speed: float | None = None
    smash_factor: float | None = None
    apex: float | None = None
    spin_rate: float | None = None
    spin_axis: float | None = None
    face_angle: float | None = None
    club_path: float | None = None
    launch_angle: float | None = None

    strike_location: StrikeLocationT | None = None
    ball_flight: str | None = None
    self_assessment: str | None = None

    tags: list[str] | None = None


class ShotCreate(ShotBase):
    shot_index: int | None = None


class ShotUpdate(ShotBase):
    pass


class BulkClubRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    club: str
    from_index: int = Field(..., ge=1)
    to_index: int = Field(..., ge=1)


class Corner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class ManualCalibration(BaseModel):
    """Four panel corners picked by hand in the review UI."""

    model_config = ConfigDict(extra="forbid")

    corners: list[Corner] = Field(..., min_length=4, max_length=4)


class InsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    max_rows: int = Field(200, ge=1, le=2000)


class InsightResponse(BaseModel):
    question: str
    sql: str | None
    explanation: str | None = None
    columns: list[str] = []
    rows: list[list] = []
    row_count: int = 0
    truncated: bool = False
    chart_hint: str | None = None
    error: str | None = None
