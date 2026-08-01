-- Golf Session Analyzer schema (spec section 5).
--
-- Design rules encoded here:
--   * Raw per-shot values only. No rolling averages, no aggregates, no
--     denormalised trend state. Everything derived is computed at query time.
--   * face_to_path is a GENERATED column: `face_angle - club_path`. Spec
--     section 2 says it is derived and never OCR'd, and section 4 says to
--     discard any OCR'd value for it. Making it generated means the pipeline
--     *cannot* write one even by mistake.
--   * Units are not implied. See app/constants.py -- the unit for every
--     column below is declared there and echoed by GET /api/metadata.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_filename        TEXT,
    recorded_at            TEXT,             -- ISO-8601
    duration_s             REAL,
    fps                    REAL,
    resolution             TEXT,             -- "1080x1920"
    calibration_transform  TEXT,             -- JSON: 3x3 perspective matrix + anchors
    status                 TEXT NOT NULL DEFAULT 'processing'
                             CHECK (status IN ('processing','needs_calibration',
                                               'extracted','confirmed','failed')),
    ingested_at            TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at            TEXT,
    source_video_path      TEXT,
    audio_path             TEXT,
    source_deleted_at      TEXT,             -- section 10 retention bookkeeping
    notes                  TEXT
);

CREATE TABLE IF NOT EXISTS shots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    shot_index        INTEGER NOT NULL,      -- 1-based, ordering within session

    impact_ts         REAL,                  -- seconds into the session video
    panel_change_ts   REAL,

    club              TEXT,
    club_source       TEXT CHECK (club_source IN ('stated','inherited','manual')),

    -- Launch monitor stats. Units in app/constants.py.
    carry             REAL,                  -- yards
    total             REAL,                  -- yards
    ball_speed        REAL,                  -- mph
    club_speed        REAL,                  -- mph
    smash_factor      REAL,                  -- ratio
    apex              REAL,                  -- feet (confirmed, not yards)
    spin_rate         REAL,                  -- rpm; 0 is stored as NULL (missing)
    spin_axis         REAL,                  -- degrees; 0 is stored as NULL
    face_angle        REAL,                  -- degrees
    club_path         REAL,                  -- degrees
    -- Rounded to 2dp: the panel reports these angles to 1dp, so anything
    -- beyond that is binary-float noise, and it would otherwise reach the UI
    -- and the dispersion plot as values like 1.7999999999999998.
    face_to_path      REAL GENERATED ALWAYS AS (ROUND(face_angle - club_path, 2)) VIRTUAL,
    launch_angle      REAL,                  -- degrees

    -- Voice-derived. strike_location exists nowhere else in the system; the
    -- simulator does not report it. This is the point of the audio pipeline.
    strike_location   TEXT CHECK (strike_location IS NULL OR strike_location IN
                        ('heel','toe','center','high','low','combination')),
    ball_flight       TEXT,
    self_assessment   TEXT,

    confidence        TEXT NOT NULL DEFAULT 'needs_review'
                        CHECK (confidence IN ('high','needs_review')),
    flags             TEXT NOT NULL DEFAULT '[]',   -- JSON array of flag keys
    reviewed_at       TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE (session_id, shot_index)
);

CREATE INDEX IF NOT EXISTS idx_shots_session   ON shots(session_id, shot_index);
CREATE INDEX IF NOT EXISTS idx_shots_club      ON shots(club);
CREATE INDEX IF NOT EXISTS idx_shots_confident ON shots(confidence);
CREATE INDEX IF NOT EXISTS idx_shots_strike    ON shots(strike_location);

CREATE TABLE IF NOT EXISTS shot_tags (
    shot_id INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    PRIMARY KEY (shot_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_shot_tags_tag ON shot_tags(tag);

CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    shot_id    INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('stat_frame','clip','transcript_segment')),
    path       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (shot_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_shot ON artifacts(shot_id);

-- Full session transcript, kept so voice attribution can be re-run without
-- re-transcribing (section 5: "Keep artifacts so any shot can be reprocessed
-- without re-ingesting the source video").
CREATE TABLE IF NOT EXISTS transcripts (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    words      TEXT NOT NULL,   -- JSON: [{word, start, end}, ...]
    text       TEXT NOT NULL,
    model      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Anomalies from stage 3 binding that never became shots: unmatched panel
-- changes and unmatched impacts. Surfaced in the review UI, not silently lost.
CREATE TABLE IF NOT EXISTS anomalies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    ts         REAL,
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_anomalies_session ON anomalies(session_id);

-- Read-only view the Phase 5 insight agent is pointed at. It sees confirmed
-- shots only, flattened with session context, so the agent cannot report on
-- unreviewed extraction output.
CREATE VIEW IF NOT EXISTS confirmed_shots AS
SELECT
    s.id                AS shot_id,
    s.session_id        AS session_id,
    ses.recorded_at     AS recorded_at,
    date(ses.recorded_at) AS session_date,
    s.shot_index        AS shot_index,
    s.club              AS club,
    s.carry             AS carry_yards,
    s.total             AS total_yards,
    s.ball_speed        AS ball_speed_mph,
    s.club_speed        AS club_speed_mph,
    s.smash_factor      AS smash_factor,
    s.apex              AS apex_feet,
    s.spin_rate         AS spin_rate_rpm,
    s.spin_axis         AS spin_axis_deg,
    s.face_angle        AS face_angle_deg,
    s.club_path         AS club_path_deg,
    s.face_to_path      AS face_to_path_deg,
    s.launch_angle      AS launch_angle_deg,
    s.strike_location   AS strike_location,
    s.ball_flight       AS ball_flight,
    s.self_assessment   AS self_assessment
FROM shots s
JOIN sessions ses ON ses.id = s.session_id
WHERE s.confidence = 'high';
