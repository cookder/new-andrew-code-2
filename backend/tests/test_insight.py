"""Phase 5 -- the insight agent's read-only guarantees.

The agent generates SQL from natural language, so the guard around it is a
security boundary rather than a nicety. Two independent layers are tested here:
the keyword/statement guard, and SQLite's own ``mode=ro`` enforcement.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.app import store
from backend.app.db import connect_readonly
from backend.app.insight.agent import answer_question
from backend.app.insight.sql_guard import UnsafeSQL, apply_limit, ensure_readonly, run_readonly

from .conftest import make_shot


class TestGuardAccepts:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM confirmed_shots",
            "select club, count(*) from confirmed_shots group by club",
            "WITH x AS (SELECT * FROM confirmed_shots) SELECT * FROM x",
            "SELECT * FROM confirmed_shots;",
            "  SELECT 1  ",
        ],
    )
    def test_plain_reads_pass(self, sql):
        assert ensure_readonly(sql)


class TestGuardRejects:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM shots",
            "UPDATE shots SET carry = 0",
            "INSERT INTO shots (id) VALUES (1)",
            "DROP TABLE shots",
            "CREATE TABLE evil (a int)",
            "ALTER TABLE shots ADD COLUMN x int",
            "PRAGMA table_info(shots)",
            "ATTACH DATABASE '/tmp/x.db' AS x",
            "VACUUM",
        ],
    )
    def test_writes_and_meta_statements(self, sql):
        with pytest.raises(UnsafeSQL):
            ensure_readonly(sql)

    def test_stacked_statements(self):
        with pytest.raises(UnsafeSQL, match="single statement"):
            ensure_readonly("SELECT 1; DROP TABLE shots")

    def test_write_hidden_after_a_comment(self):
        with pytest.raises(UnsafeSQL):
            ensure_readonly("SELECT 1; -- ok\nDROP TABLE shots")

    def test_write_inside_a_subquery(self):
        with pytest.raises(UnsafeSQL):
            ensure_readonly("SELECT * FROM (DELETE FROM shots RETURNING *)")

    def test_empty_query(self):
        with pytest.raises(UnsafeSQL):
            ensure_readonly("   ")

    def test_comment_only_query(self):
        with pytest.raises(UnsafeSQL):
            ensure_readonly("-- just a comment")


class TestGuardFalsePositives:
    def test_keyword_inside_a_string_literal_is_allowed(self):
        # "drop" appears in the golfer's own words often enough that rejecting
        # it as a keyword would break legitimate questions.
        sql = "SELECT * FROM confirmed_shots WHERE ball_flight = 'dropped out of the sky'"
        assert ensure_readonly(sql)

    def test_keyword_as_a_substring_is_allowed(self):
        assert ensure_readonly("SELECT dropped_count FROM confirmed_shots")

    def test_keyword_inside_a_comment_is_allowed(self):
        assert ensure_readonly("SELECT 1 /* do not drop anything */")


class TestLimit:
    def test_wraps_without_editing_the_query(self):
        # The user is shown the inner query; rewriting its text would make
        # "the SQL is always visible" a lie.
        wrapped = apply_limit("SELECT * FROM confirmed_shots", 10)
        assert "SELECT * FROM confirmed_shots" in wrapped
        assert wrapped.strip().endswith("LIMIT 11")

    def test_truncation_is_reported(self, conn, session):
        for _ in range(5):
            make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        readonly = connect_readonly()
        try:
            _, rows, truncated = run_readonly(readonly, "SELECT * FROM confirmed_shots", 3)
        finally:
            readonly.close()
        assert len(rows) == 3
        assert truncated is True

    def test_no_truncation_when_under_limit(self, conn, session):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        readonly = connect_readonly()
        try:
            _, rows, truncated = run_readonly(readonly, "SELECT * FROM confirmed_shots", 10)
        finally:
            readonly.close()
        assert len(rows) == 1
        assert truncated is False


class TestReadOnlyConnection:
    def test_driver_rejects_writes_even_if_the_guard_were_bypassed(self, conn, session):
        make_shot(conn, session["id"])
        readonly = connect_readonly()
        try:
            with pytest.raises(sqlite3.OperationalError):
                readonly.execute("DELETE FROM shots")
        finally:
            readonly.close()

    def test_reads_work(self, conn, session):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        readonly = connect_readonly()
        try:
            n = readonly.execute("SELECT COUNT(*) AS n FROM confirmed_shots").fetchone()["n"]
        finally:
            readonly.close()
        assert n == 1


class TestConfirmedShotsView:
    def test_hides_unreviewed_shots(self, conn, session):
        make_shot(conn, session["id"])
        readonly = connect_readonly()
        try:
            rows = readonly.execute("SELECT * FROM confirmed_shots").fetchall()
        finally:
            readonly.close()
        assert rows == []

    def test_column_names_carry_units(self, conn, session):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        readonly = connect_readonly()
        try:
            cursor = readonly.execute("SELECT * FROM confirmed_shots")
            columns = {d[0] for d in cursor.description}
        finally:
            readonly.close()
        # The agent reads these names; an unlabelled "apex" would invite the
        # yards/feet confusion the spec calls out explicitly.
        assert "apex_feet" in columns
        assert "carry_yards" in columns
        assert "ball_speed_mph" in columns
        assert "strike_location" in columns


class TestAnswerQuestion:
    def test_missing_api_key_is_reported_not_raised(self, conn, session):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        result = answer_question("what is my average carry?")
        assert result["error"]
        assert result["rows"] == []

    def test_sql_is_returned_even_when_execution_fails(self, conn, session, monkeypatch):
        make_shot(conn, session["id"])
        store.confirm_session(conn, session["id"])
        monkeypatch.setattr(
            "backend.app.insight.agent.generate_sql",
            lambda q, ddl: {
                "sql": "SELECT * FROM no_such_table",
                "explanation": "nope",
                "chart_hint": "table",
            },
        )
        result = answer_question("anything")
        assert result["sql"] == "SELECT * FROM no_such_table"
        assert "query failed" in result["error"]

    def test_sql_is_returned_when_the_guard_rejects_it(self, conn, monkeypatch):
        monkeypatch.setattr(
            "backend.app.insight.agent.generate_sql",
            lambda q, ddl: {
                "sql": "DROP TABLE shots",
                "explanation": "",
                "chart_hint": "table",
            },
        )
        result = answer_question("delete everything")
        assert result["sql"] == "DROP TABLE shots"
        assert "rejected" in result["error"]

    def test_successful_round_trip(self, conn, session, monkeypatch):
        for _ in range(3):
            make_shot(conn, session["id"], club="7 iron")
        store.confirm_session(conn, session["id"])
        monkeypatch.setattr(
            "backend.app.insight.agent.generate_sql",
            lambda q, ddl: {
                "sql": "SELECT club, COUNT(*) AS n FROM confirmed_shots GROUP BY club",
                "explanation": "Shot count per club.",
                "chart_hint": "bar",
            },
        )
        result = answer_question("how many shots per club?")
        assert result["error"] is None
        assert result["columns"] == ["club", "n"]
        assert result["rows"] == [["7 iron", 3]]
        assert result["chart_hint"] == "bar"
