# Golf Session Analyzer

Ingests range session videos recorded on a Full Swing simulator, extracts per-shot
launch monitor stats via OCR, extracts club and subjective notes from spoken audio,
persists to a local database, and surfaces trends over time.

Single user. Local-first. No auth, no multi-tenancy, no cloud deployment.

---

## What is here

Built in the order section 8 of the spec calls non-negotiable, because each phase
is independently useful and the later ones are the fragile ones:

| Phase | What | State |
|---|---|---|
| 1 | Schema, manual entry, review UI, analysis layer | Complete and exercised end to end |
| 2 | Audio: impact detection, transcription, voice attribution | Implemented; pure logic tested, media path unrun here |
| 3 | Vision: calibration, panel diffing, stat extraction, validation | Implemented; pure logic tested, media path unrun here |
| 4 | Clip extraction | Implemented; unrun here |
| 5 | Read-only text-to-SQL insight agent | Complete; guard and round trip tested with a stubbed model |

**Verified in this repo:** 173 passing tests, a frontend that typechecks and builds
clean, and every screen rendered against seeded data in both light and dark mode.

**Not verified in this repo:** anything that needs a real MP4, `ffmpeg`, or an
Anthropic API key — none of the three exist in this environment. That covers frame
grabbing, panel-border detection, Whisper transcription, clip cutting, and the live
vision/voice/insight model calls. The code around them is written and their pure
logic (binding, club persistence, validation, the SQL guard) is tested directly;
the media I/O itself is untested and should be treated as first-draft until it has
seen a real session.

---

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .                 # API + analysis + review UI
.venv/bin/pip install -e '.[llm]'          # + phases 3, 5, 6 (anthropic SDK)
.venv/bin/pip install -e '.[media]'        # + phases 2, 3, 4 (ffmpeg, whisper, opencv)
.venv/bin/pip install -e '.[dev]'          # + pytest

cd frontend && npm install
```

`ffmpeg` and `ffprobe` must be on `PATH` for ingest and clip extraction.
`ANTHROPIC_API_KEY` must be set for stat extraction, voice attribution, and the
insight agent.

## Running

```bash
# Seed three confirmed sessions plus one waiting in review
.venv/bin/python -m backend.app.seed --reset

# API on :8000
.venv/bin/python -m uvicorn backend.app.api:app --reload

# UI on :5173 (proxies /api to :8000)
cd frontend && npm run dev

# Tests
.venv/bin/python -m pytest backend/tests -q
```

Ingest a video by dropping an MP4 into the watched folder and running:

```bash
.venv/bin/python -m backend.app.pipeline.watcher --once
```

Paths default under `~/.golf-analyzer` and are overridable with `GOLF_DATA_DIR`,
`GOLF_DB_PATH`, `GOLF_WATCH_DIR`, `GOLF_MEDIA_DIR`, `GOLF_ARTIFACT_DIR`.

---

## The parts worth knowing about

### The panel shows the previous shot

This one fact drives the whole detection stage. The stat panel updates 2–3s after
impact and displays the shot that just finished, so the panel is never read at swing
time. A panel change is the authoritative "a shot completed" event and binds
*backwards* to the nearest preceding impact within 10s. Unmatched panel changes and
unmatched impacts are both recorded as anomalies rather than dropped — including the
last-shot-of-the-session case, where recording stopped before the panel refreshed.

`bind_shots()` in `pipeline/shot_detection.py` is pure and has its own test file.

### Face-to-path cannot be OCR'd into the database

The spec says face-to-path is derived, never read. That is enforced by the schema
rather than by convention: `face_to_path` is a SQLite `GENERATED ALWAYS AS
(ROUND(face_angle - club_path, 2)) VIRTUAL` column, so a write is a hard error. The
row-6 OCR reading is still taken when the faded row is legible, but it is used only
as a cross-check against the derived value — which is what makes section 3's second
identity a real verification rather than a tautology.

### Zero spin is missing, not zero

Spin rate and spin axis read zero on this unit. They are normalized to `NULL` once,
at the single write path, so no aggregate anywhere averages in a fake zero. The
per-club view shows a separate `n=` for any metric whose sample is smaller than the
club's shot count, which is how a spin median built on 12 of 17 shots announces
itself.

### Nothing reaches analysis unreviewed

`confidence` is only ever raised in `store.confirm_session`. Every analysis query
filters to `confidence = 'high'`, and the insight agent is pointed at a
`confirmed_shots` view rather than the `shots` table, so unreviewed extraction output
is not in its world at all.

### The insight agent is read-only twice over

The connection is opened `mode=ro` (SQLite's own enforcement, not ours) *and* the
generated SQL passes a guard that rejects stacked statements, writes, `PRAGMA`,
`ATTACH`, and friends — with comments and string literals stripped first, so neither
a commented-out `DROP` nor the word "dropped" inside a golfer's own description
confuses it. The SQL is returned to the UI whether it ran, failed, or was rejected.

### Units are stated once

`app/constants.py` is the only place a unit appears. The API republishes it at
`GET /api/metadata` and the frontend reads it from there, so "apex is in feet" is
written down exactly once in the whole system.

---

## Layout

```
backend/app/
  constants.py        units, panel grid, tolerances, enums, flags — the single source
  schema.sql          tables, the generated column, the confirmed_shots view
  validation.py       section 3: the two identities, ranges, zero-spin, club-change guard
  store.py            CRUD; every write path re-validates
  analysis.py         section 7: the five views, median/IQR, the trend gate
  api.py              FastAPI surface
  seed.py             three confirmed sessions + one in review
  pipeline/
    ingest.py         stage 1  probe, extract audio, create session
    calibration.py    stage 2  anchors -> perspective transform, or halt
    shot_detection.py stage 3  panel diff + audio onset -> bound shots
    stat_extraction.py stage 4 vision OCR by grid position
    transcription.py  stage 5  faster-whisper with a club vocabulary hint
    voice.py          stage 6  club persistence + unannounced-change guard
    clips.py          stage 7  impact-anchored clips
    runner.py         orchestration; deliberately a plain function
    watcher.py        the watched folder
  insight/
    sql_guard.py      statement/keyword guard
    agent.py          phase 5 text-to-SQL
frontend/src/
  pages/              review UI + the five analysis views + ask
```

---

## Open branch: data export

Section 11 of the spec: if the Full Swing setup supports CSV or session export,
sections 2 and 3 and the whole of phase 3 become unnecessary, and stats would join
to the audio track by timestamp instead. **Check this before investing further in
the vision pipeline** — it only ever removes work, and phases 1, 2, 4, and 5 are
unaffected either way.

## Flagged defaults

Per section 12, revisit if wrong:

1. The final shot of a session is dropped if the panel does not refresh on camera.
   Mitigated by recording five seconds past the last swing, not by code — but it is
   reported as an unmatched-impact anomaly rather than silently lost.
2. Zero-valued spin rate and spin axis are treated as missing rather than measured.
3. Club inheritance is trusted, with ball-speed deviation as the only guard.
4. No trend surfaced below 30 shots per club across 3 sessions. Arbitrary; tune once
   real volume exists. The constant is `TREND_MIN_SHOTS` / `TREND_MIN_SESSIONS`.
