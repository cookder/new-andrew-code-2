"""Regression: connections must survive being handed between threads.

FastAPI runs a sync dependency and the sync endpoint that consumes it on
different threadpool threads. A connection opened with SQLite's default
``check_same_thread=True`` raises ``ProgrammingError`` the moment the handler
touches it, which takes down every endpoint at runtime while the in-process
TestClient stays green.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from backend.app import store
from backend.app.db import connect, connect_readonly, init_db

from .conftest import make_shot


def test_connection_is_usable_from_another_thread(isolated_data_dir):
    init_db()
    conn = connect()
    try:
        session = store.create_session(conn, source_filename="threaded.mp4")

        with ThreadPoolExecutor(max_workers=1) as pool:
            shot = pool.submit(make_shot, conn, session["id"]).result()
        assert shot["id"]

        with ThreadPoolExecutor(max_workers=1) as pool:
            shots = pool.submit(store.list_shots, conn, session["id"]).result()
        assert len(shots) == 1
    finally:
        conn.close()


def test_readonly_connection_is_usable_from_another_thread(isolated_data_dir):
    init_db()
    conn = connect()
    try:
        session = store.create_session(conn, source_filename="threaded.mp4")
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
    finally:
        conn.close()

    readonly = connect_readonly()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            rows = pool.submit(
                lambda: readonly.execute("SELECT * FROM confirmed_shots").fetchall()
            ).result()
        assert len(rows) == 1
    finally:
        readonly.close()
