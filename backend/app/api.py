"""FastAPI surface. Local, single user, no auth (spec: "No auth, no
multi-tenancy, no cloud deployment")."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import analysis, store
from .config import settings
from .constants import (
    ARTIFACT_KINDS,
    CLUB_VOCABULARY,
    CLUB_SOURCES,
    FLAG_DESCRIPTIONS,
    METRICS,
    PANEL_GRID,
    SESSION_STATUSES,
    STRIKE_LOCATIONS,
    TREND_MIN_SESSIONS,
    TREND_MIN_SHOTS,
    UNITS,
)
from .db import connect, init_db
from .insight.agent import answer_question
from .models import (
    BulkClubRequest,
    InsightRequest,
    InsightResponse,
    ManualCalibration,
    SessionCreate,
    SessionUpdate,
    ShotCreate,
    ShotUpdate,
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_dirs()
    init_db()
    yield


app = FastAPI(title="Golf Session Analyzer", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


DB = Depends(get_db)


# --------------------------------------------------------------------------
# Metadata -- the frontend reads units and enums from here rather than
# hardcoding them, so section 2's "never let a unit be implied" holds in the UI
# too.
# --------------------------------------------------------------------------


@app.get("/api/metadata")
def metadata() -> dict[str, Any]:
    return {
        "metrics": [
            {
                "key": m.key,
                "label": m.label,
                "unit": m.unit,
                "decimals": m.decimals,
                "derived": m.derived,
                "zero_is_missing": m.zero_is_missing,
                "sane_min": m.sane_min,
                "sane_max": m.sane_max,
            }
            for m in METRICS
        ],
        "units": UNITS,
        "panel_grid": [list(row) for row in PANEL_GRID],
        "strike_locations": list(STRIKE_LOCATIONS),
        "club_sources": list(CLUB_SOURCES),
        "session_statuses": list(SESSION_STATUSES),
        "artifact_kinds": list(ARTIFACT_KINDS),
        "clubs": list(CLUB_VOCABULARY),
        "flag_descriptions": FLAG_DESCRIPTIONS,
        "trend_gate": {"min_shots": TREND_MIN_SHOTS, "min_sessions": TREND_MIN_SESSIONS},
    }


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


@app.get("/api/sessions")
def get_sessions(conn: sqlite3.Connection = DB) -> list[dict]:
    return store.list_sessions(conn)


@app.post("/api/sessions", status_code=201)
def post_session(body: SessionCreate, conn: sqlite3.Connection = DB) -> dict:
    return store.create_session(conn, **body.model_dump(exclude_none=True))


@app.get("/api/sessions/{session_id}")
def get_one_session(session_id: int, conn: sqlite3.Connection = DB) -> dict:
    session = store.get_session(conn, session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    session["shots"] = store.list_shots(conn, session_id)
    session["anomalies"] = store.list_anomalies(conn, session_id)
    return session


@app.patch("/api/sessions/{session_id}")
def patch_session(
    session_id: int, body: SessionUpdate, conn: sqlite3.Connection = DB
) -> dict:
    if store.get_session(conn, session_id) is None:
        raise HTTPException(404, "session not found")
    return store.update_session(conn, session_id, **body.model_dump(exclude_unset=True))  # type: ignore[return-value]


@app.delete("/api/sessions/{session_id}", status_code=204)
def remove_session(session_id: int, conn: sqlite3.Connection = DB) -> None:
    store.delete_session(conn, session_id)


@app.post("/api/sessions/{session_id}/confirm")
def confirm(session_id: int, conn: sqlite3.Connection = DB) -> dict:
    if store.get_session(conn, session_id) is None:
        raise HTTPException(404, "session not found")
    return store.confirm_session(conn, session_id)


@app.post("/api/sessions/{session_id}/unconfirm")
def unconfirm(session_id: int, conn: sqlite3.Connection = DB) -> dict:
    if store.get_session(conn, session_id) is None:
        raise HTTPException(404, "session not found")
    return store.unconfirm_session(conn, session_id)


# --------------------------------------------------------------------------
# Shots
# --------------------------------------------------------------------------


@app.get("/api/sessions/{session_id}/shots")
def get_shots(session_id: int, conn: sqlite3.Connection = DB) -> list[dict]:
    return store.list_shots(conn, session_id)


@app.post("/api/sessions/{session_id}/shots", status_code=201)
def post_shot(session_id: int, body: ShotCreate, conn: sqlite3.Connection = DB) -> dict:
    if store.get_session(conn, session_id) is None:
        raise HTTPException(404, "session not found")
    return store.create_shot(conn, session_id, **body.model_dump(exclude_unset=True))


@app.get("/api/shots/{shot_id}")
def get_one_shot(shot_id: int, conn: sqlite3.Connection = DB) -> dict:
    shot = store.get_shot(conn, shot_id)
    if shot is None:
        raise HTTPException(404, "shot not found")
    return shot


@app.patch("/api/shots/{shot_id}")
def patch_shot(shot_id: int, body: ShotUpdate, conn: sqlite3.Connection = DB) -> dict:
    shot = store.update_shot(conn, shot_id, **body.model_dump(exclude_unset=True))
    if shot is None:
        raise HTTPException(404, "shot not found")
    return shot


@app.delete("/api/shots/{shot_id}", status_code=204)
def remove_shot(shot_id: int, conn: sqlite3.Connection = DB) -> None:
    store.delete_shot(conn, shot_id)


@app.post("/api/sessions/{session_id}/bulk-club")
def bulk_club(
    session_id: int, body: BulkClubRequest, conn: sqlite3.Connection = DB
) -> dict:
    """Reassign a contiguous range of shots to one club in a single action --
    the repair for an unannounced club switch (spec section 6)."""
    if store.get_session(conn, session_id) is None:
        raise HTTPException(404, "session not found")
    updated = store.bulk_set_club(
        conn, session_id, body.club, from_index=body.from_index, to_index=body.to_index
    )
    return {"updated": updated, "shots": store.list_shots(conn, session_id)}


@app.post("/api/sessions/{session_id}/recheck-clubs")
def recheck_clubs(session_id: int, conn: sqlite3.Connection = DB) -> list[dict]:
    store.rerun_club_change_guard(conn, session_id)
    return store.list_shots(conn, session_id)


# --------------------------------------------------------------------------
# Analysis (spec section 7)
# --------------------------------------------------------------------------


@app.get("/api/analysis/per-club")
def analysis_per_club(conn: sqlite3.Connection = DB) -> dict:
    return analysis.per_club(conn)


@app.get("/api/analysis/strike-vs-outcome")
def analysis_strike(club: str | None = None, conn: sqlite3.Connection = DB) -> dict:
    return analysis.strike_vs_outcome(conn, club)


@app.get("/api/analysis/trend")
def analysis_trend(metric: str = "carry", conn: sqlite3.Connection = DB) -> dict:
    try:
        return analysis.session_trend(conn, metric)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/analysis/dispersion")
def analysis_dispersion(club: str | None = None, conn: sqlite3.Connection = DB) -> dict:
    return analysis.dispersion(conn, club)


@app.get("/api/analysis/facets")
def analysis_facets(conn: sqlite3.Connection = DB) -> dict:
    return analysis.facets(conn)


@app.get("/api/shots")
def browse_shots(
    club: str | None = None,
    strike_location: str | None = None,
    session_id: int | None = None,
    tag: str | None = None,
    flagged: bool | None = None,
    confirmed_only: bool = True,
    date_from: str | None = None,
    date_to: str | None = None,
    text: str | None = None,
    carry_min: float | None = None,
    carry_max: float | None = None,
    ball_speed_min: float | None = None,
    ball_speed_max: float | None = None,
    smash_factor_min: float | None = None,
    smash_factor_max: float | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = DB,
) -> dict:
    return analysis.shot_browser(
        conn,
        club=club,
        strike_location=strike_location,
        session_id=session_id,
        tag=tag,
        flagged=flagged,
        confirmed_only=confirmed_only,
        date_from=date_from,
        date_to=date_to,
        text=text,
        limit=limit,
        offset=offset,
        carry_min=carry_min,
        carry_max=carry_max,
        ball_speed_min=ball_speed_min,
        ball_speed_max=ball_speed_max,
        smash_factor_min=smash_factor_min,
        smash_factor_max=smash_factor_max,
    )


# --------------------------------------------------------------------------
# Artifacts -- every stat row is one click from the swing that produced it
# --------------------------------------------------------------------------


@app.get("/api/artifacts/{artifact_id}/file")
def artifact_file(artifact_id: int, conn: sqlite3.Connection = DB) -> FileResponse:
    row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "artifact not found")
    path = Path(row["path"])
    if not path.is_file():
        raise HTTPException(410, "artifact file is gone from disk")
    return FileResponse(path)


# --------------------------------------------------------------------------
# Insight agent (spec phase 5)
# --------------------------------------------------------------------------


@app.post("/api/insight/ask", response_model=InsightResponse)
def insight(body: InsightRequest) -> InsightResponse:
    return InsightResponse(**answer_question(body.question, max_rows=body.max_rows))


# --------------------------------------------------------------------------
# Pipeline (phases 2-4). Endpoints are thin -- the work lives in app.pipeline.
# --------------------------------------------------------------------------


@app.post("/api/sessions/{session_id}/calibration")
def set_calibration(
    session_id: int, body: ManualCalibration, conn: sqlite3.Connection = DB
) -> dict:
    """Manual corner selection, used when automatic anchor detection halts a
    session (spec section 4, stage 2: "Do not guess")."""
    if store.get_session(conn, session_id) is None:
        raise HTTPException(404, "session not found")
    from .pipeline.calibration import set_manual_corners

    corners = [(c.x, c.y) for c in body.corners]
    return set_manual_corners(conn, session_id, corners)


@app.get("/api/sessions/{session_id}/anomalies")
def session_anomalies(session_id: int, conn: sqlite3.Connection = DB) -> list[dict]:
    return store.list_anomalies(conn, session_id)


@app.post("/api/pipeline/scan")
def pipeline_scan(with_clips: bool = True) -> list[dict]:
    """Sweep the watched folder once and run the pipeline on anything new."""
    from .pipeline.watcher import watch_once

    return watch_once(with_clips=with_clips)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
