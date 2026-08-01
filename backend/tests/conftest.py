from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every path setting at a scratch directory for the duration of a test.

    ``Settings`` reads these lazily on each property access, so setting them
    here takes effect even though the module was imported long ago.
    """
    monkeypatch.setenv("GOLF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOLF_DB_PATH", str(tmp_path / "golf.db"))
    monkeypatch.setenv("GOLF_WATCH_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("GOLF_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("GOLF_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield tmp_path


@pytest.fixture
def conn(isolated_data_dir) -> sqlite3.Connection:
    from backend.app.db import connect, init_db

    init_db()
    connection = connect()
    yield connection
    connection.close()


@pytest.fixture
def session(conn):
    from backend.app import store

    return store.create_session(
        conn, source_filename="test.mp4", recorded_at="2026-07-01T17:00:00"
    )


def make_shot(conn, session_id: int, **overrides):
    """A valid shot that passes every section-3 check unless overridden."""
    from backend.app import store

    values = {
        "club": "7 iron",
        "club_source": "stated",
        "carry": 162.0,
        "total": 170.0,
        "ball_speed": 118.4,
        "club_speed": 84.0,
        "smash_factor": 1.41,
        "apex": 88.0,
        "spin_rate": 6800.0,
        "spin_axis": -2.1,
        "face_angle": 1.2,
        "club_path": -0.6,
        "launch_angle": 18.4,
        "strike_location": "center",
    }
    values.update(overrides)
    return store.create_shot(conn, session_id, **values)


@pytest.fixture
def client(isolated_data_dir):
    from fastapi.testclient import TestClient

    from backend.app.api import app

    with TestClient(app) as test_client:
        yield test_client
