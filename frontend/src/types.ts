export type StrikeLocation =
  | 'heel'
  | 'toe'
  | 'center'
  | 'high'
  | 'low'
  | 'combination'

export type ClubSource = 'stated' | 'inherited' | 'manual'
export type Confidence = 'high' | 'needs_review'
export type SessionStatus =
  | 'processing'
  | 'needs_calibration'
  | 'extracted'
  | 'confirmed'
  | 'failed'

export interface Metric {
  key: string
  label: string
  unit: string
  decimals: number
  derived: boolean
  zero_is_missing: boolean
  sane_min: number | null
  sane_max: number | null
}

export interface Metadata {
  metrics: Metric[]
  units: Record<string, string>
  panel_grid: string[][]
  strike_locations: StrikeLocation[]
  club_sources: ClubSource[]
  session_statuses: SessionStatus[]
  artifact_kinds: string[]
  clubs: string[]
  flag_descriptions: Record<string, string>
  trend_gate: { min_shots: number; min_sessions: number }
}

export interface Artifact {
  id: number
  shot_id: number
  kind: string
  path: string
}

export interface Shot {
  id: number
  session_id: number
  shot_index: number
  impact_ts: number | null
  panel_change_ts: number | null
  club: string | null
  club_source: ClubSource | null
  carry: number | null
  total: number | null
  ball_speed: number | null
  club_speed: number | null
  smash_factor: number | null
  apex: number | null
  spin_rate: number | null
  spin_axis: number | null
  face_angle: number | null
  club_path: number | null
  face_to_path: number | null
  launch_angle: number | null
  strike_location: StrikeLocation | null
  ball_flight: string | null
  self_assessment: string | null
  confidence: Confidence
  flags: string[]
  reviewed_at: string | null
  created_at: string
  tags: string[]
  artifacts: Artifact[]
}

export interface Session {
  id: number
  source_filename: string | null
  recorded_at: string | null
  duration_s: number | null
  fps: number | null
  resolution: string | null
  status: SessionStatus
  ingested_at: string
  reviewed_at: string | null
  notes: string | null
  shot_count?: number
  flagged_count?: number
  confirmed_count?: number
}

export interface SessionDetail extends Session {
  shots: Shot[]
  anomalies: Anomaly[]
}

export interface Anomaly {
  id: number
  session_id: number
  kind: string
  ts: number | null
  detail: string
}

export interface Summary {
  n: number
  median: number | null
  q1: number | null
  q3: number | null
  iqr: number | null
  min: number | null
  max: number | null
}

export interface ClubSummary {
  club: string | null
  shot_count: number
  session_count: number
  metrics: Record<string, Summary>
}

export interface PerClub {
  units: Record<string, string>
  clubs: ClubSummary[]
}

export interface StrikeBucket {
  strike_location: StrikeLocation | null
  shot_count: number
  smash_factor: Summary
  carry: Summary
  ball_speed: Summary
  apex: Summary
  dispersion: {
    carry_iqr: number | null
    spin_axis_iqr: number | null
    carry_range: number | null
  }
}

export interface StrikeClub {
  club: string | null
  shot_count: number
  attributed_count: number
  locations: StrikeBucket[]
}

export interface StrikeVsOutcome {
  units: Record<string, string>
  clubs: StrikeClub[]
}

export interface TrendPoint extends Summary {
  session_id: number
  recorded_at: string | null
  session_date: string | null
  shot_count: number
}

export interface TrendClub {
  club: string | null
  shot_count: number
  session_count: number
  trend_eligible: boolean
  trend_gate: {
    min_shots: number
    min_sessions: number
    shots_short_by: number
    sessions_short_by: number
  }
  points: TrendPoint[]
}

export interface Trend {
  metric: string
  unit: string
  label: string
  clubs: TrendClub[]
}

export interface DispersionPoint {
  shot_id: number
  session_id: number
  club: string | null
  face_angle: number
  club_path: number
  face_to_path: number | null
  strike_location: StrikeLocation | null
  carry: number | null
  smash_factor: number | null
}

export interface Dispersion {
  units: Record<string, string>
  clubs: (string | null)[]
  point_count: number
  points: DispersionPoint[]
}

export interface BrowseResult {
  total: number
  limit: number
  offset: number
  shots: (Shot & {
    recorded_at: string | null
    source_filename: string | null
    artifacts: Record<string, string>
  })[]
}

export interface Facets {
  clubs: string[]
  tags: string[]
  strike_locations: StrikeLocation[]
}

export interface InsightAnswer {
  question: string
  sql: string | null
  explanation: string | null
  columns: string[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  chart_hint: string | null
  error: string | null
}
