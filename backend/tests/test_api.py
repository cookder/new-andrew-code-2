"""HTTP surface."""

from __future__ import annotations

VALID_SHOT = {
    "club": "7 iron",
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


def make_session(client, **overrides):
    body = {"source_filename": "test.mp4", "recorded_at": "2026-07-01T17:00:00"}
    body.update(overrides)
    return client.post("/api/sessions", json=body).json()


def add_shot(client, session_id, **overrides):
    body = {**VALID_SHOT, **overrides}
    response = client.post(f"/api/sessions/{session_id}/shots", json=body)
    assert response.status_code == 201, response.text
    return response.json()


class TestMetadata:
    def test_publishes_units_so_the_ui_never_hardcodes_them(self, client):
        meta = client.get("/api/metadata").json()
        assert meta["units"]["apex"] == "feet"
        assert meta["units"]["carry"] == "yards"

    def test_publishes_the_panel_grid(self, client):
        grid = client.get("/api/metadata").json()["panel_grid"]
        assert grid[0] == ["carry", "total"]
        assert grid[5] == ["face_to_path", "launch_angle"]

    def test_publishes_flag_explanations(self, client):
        # Section 6: a flagged row must name the specific failure.
        flags = client.get("/api/metadata").json()["flag_descriptions"]
        assert "smash_mismatch" in flags
        assert flags["smash_mismatch"]

    def test_publishes_the_trend_gate(self, client):
        gate = client.get("/api/metadata").json()["trend_gate"]
        assert gate == {"min_shots": 30, "min_sessions": 3}


class TestSessions:
    def test_create_and_fetch(self, client):
        session = make_session(client)
        assert session["status"] == "processing"
        fetched = client.get(f"/api/sessions/{session['id']}").json()
        assert fetched["source_filename"] == "test.mp4"
        assert fetched["shots"] == []

    def test_list_includes_review_counts(self, client):
        session = make_session(client)
        add_shot(client, session["id"])
        add_shot(client, session["id"], smash_factor=1.55)
        row = client.get("/api/sessions").json()[0]
        assert row["shot_count"] == 2
        assert row["flagged_count"] == 1
        assert row["confirmed_count"] == 0

    def test_patch(self, client):
        session = make_session(client)
        updated = client.patch(
            f"/api/sessions/{session['id']}", json={"notes": "windy"}
        ).json()
        assert updated["notes"] == "windy"

    def test_delete(self, client):
        session = make_session(client)
        assert client.delete(f"/api/sessions/{session['id']}").status_code == 204
        assert client.get(f"/api/sessions/{session['id']}").status_code == 404

    def test_missing_session_is_404(self, client):
        assert client.get("/api/sessions/999").status_code == 404
        assert client.patch("/api/sessions/999", json={"notes": "x"}).status_code == 404
        assert client.post("/api/sessions/999/confirm").status_code == 404


class TestShots:
    def test_manual_entry(self, client):
        session = make_session(client)
        shot = add_shot(client, session["id"])
        assert shot["club_source"] == "manual"
        assert shot["flags"] == []

    def test_face_to_path_is_derived_and_not_accepted_as_input(self, client):
        session = make_session(client)
        shot = add_shot(client, session["id"])
        assert shot["face_to_path"] == 1.8

        rejected = client.post(
            f"/api/sessions/{session['id']}/shots",
            json={**VALID_SHOT, "face_to_path": 99},
        )
        assert rejected.status_code == 422

    def test_validation_flags_are_returned(self, client):
        session = make_session(client)
        shot = add_shot(client, session["id"], smash_factor=1.55)
        assert "smash_mismatch" in shot["flags"]

    def test_inline_edit(self, client):
        session = make_session(client)
        shot = add_shot(client, session["id"], smash_factor=1.55)
        fixed = client.patch(f"/api/shots/{shot['id']}", json={"smash_factor": 1.41}).json()
        assert fixed["flags"] == []

    def test_invalid_strike_location_rejected(self, client):
        session = make_session(client)
        response = client.post(
            f"/api/sessions/{session['id']}/shots",
            json={**VALID_SHOT, "strike_location": "shank"},
        )
        assert response.status_code == 422

    def test_delete(self, client):
        session = make_session(client)
        shot = add_shot(client, session["id"])
        assert client.delete(f"/api/shots/{shot['id']}").status_code == 204
        assert client.get(f"/api/shots/{shot['id']}").status_code == 404


class TestBulkClub:
    def test_reassigns_a_range(self, client):
        session = make_session(client)
        for _ in range(4):
            add_shot(client, session["id"], club="7 iron")
        response = client.post(
            f"/api/sessions/{session['id']}/bulk-club",
            json={"club": "8 iron", "from_index": 2, "to_index": 3},
        ).json()
        assert response["updated"] == 2
        assert [s["club"] for s in response["shots"]] == [
            "7 iron",
            "8 iron",
            "8 iron",
            "7 iron",
        ]

    def test_rejects_a_zero_index(self, client):
        session = make_session(client)
        response = client.post(
            f"/api/sessions/{session['id']}/bulk-club",
            json={"club": "8 iron", "from_index": 0, "to_index": 1},
        )
        assert response.status_code == 422


class TestConfirmGate:
    def test_analysis_is_empty_until_a_session_is_confirmed(self, client):
        session = make_session(client)
        add_shot(client, session["id"])
        assert client.get("/api/analysis/per-club").json()["clubs"] == []

        client.post(f"/api/sessions/{session['id']}/confirm")
        clubs = client.get("/api/analysis/per-club").json()["clubs"]
        assert clubs[0]["shot_count"] == 1

    def test_unconfirm_reverses_it(self, client):
        session = make_session(client)
        add_shot(client, session["id"])
        client.post(f"/api/sessions/{session['id']}/confirm")
        client.post(f"/api/sessions/{session['id']}/unconfirm")
        assert client.get("/api/analysis/per-club").json()["clubs"] == []


class TestAnalysisEndpoints:
    def _confirmed_session(self, client, count=3, **overrides):
        session = make_session(client)
        for _ in range(count):
            add_shot(client, session["id"], **overrides)
        client.post(f"/api/sessions/{session['id']}/confirm")
        return session

    def test_per_club(self, client):
        self._confirmed_session(client)
        body = client.get("/api/analysis/per-club").json()
        assert body["clubs"][0]["metrics"]["carry"]["median"] == 162.0

    def test_strike_vs_outcome(self, client):
        self._confirmed_session(client)
        body = client.get("/api/analysis/strike-vs-outcome").json()
        assert body["clubs"][0]["locations"][0]["strike_location"] == "center"

    def test_trend_reports_the_gate(self, client):
        self._confirmed_session(client)
        club = client.get("/api/analysis/trend?metric=carry").json()["clubs"][0]
        assert club["trend_eligible"] is False
        assert club["trend_gate"]["shots_short_by"] == 27

    def test_trend_rejects_an_unknown_metric(self, client):
        assert client.get("/api/analysis/trend?metric=vibes").status_code == 400

    def test_dispersion(self, client):
        self._confirmed_session(client)
        body = client.get("/api/analysis/dispersion").json()
        assert body["point_count"] == 3
        assert body["points"][0]["face_to_path"] == 1.8

    def test_facets(self, client):
        self._confirmed_session(client)
        body = client.get("/api/analysis/facets").json()
        assert body["clubs"] == ["7 iron"]
        assert "center" in body["strike_locations"]

    def test_shot_browser(self, client):
        self._confirmed_session(client)
        body = client.get("/api/shots?club=7%20iron").json()
        assert body["total"] == 3


class TestArtifacts:
    def test_missing_artifact_is_404(self, client):
        assert client.get("/api/artifacts/999/file").status_code == 404

    def test_artifact_whose_file_is_gone_reports_410(self, client, isolated_data_dir):
        from backend.app import store
        from backend.app.db import connect

        session = make_session(client)
        shot = add_shot(client, session["id"])
        conn = connect()
        try:
            artifact = store.add_artifact(
                conn, shot["id"], "clip", str(isolated_data_dir / "gone.mp4")
            )
        finally:
            conn.close()
        assert client.get(f"/api/artifacts/{artifact['id']}/file").status_code == 410


class TestHealth:
    def test_health(self, client):
        assert client.get("/api/health").json() == {"ok": True}
