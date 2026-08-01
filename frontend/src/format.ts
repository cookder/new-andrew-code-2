import type { Metadata, Summary } from './types'

/** Fixed categorical slot order. Assigned by entity, never cycled. */
export const SERIES = [
  'var(--series-1)',
  'var(--series-2)',
  'var(--series-3)',
  'var(--series-4)',
  'var(--series-5)',
  'var(--series-6)',
  'var(--series-7)',
  'var(--series-8)',
] as const

export const MAX_SERIES = SERIES.length

/**
 * A stable colour for a club.
 *
 * Colour follows the entity, not its rank: the index comes from the full
 * ordered club list, so filtering the chart down to two clubs leaves the
 * survivors on the hues they already had.
 */
export function seriesColor(club: string | null, allClubs: (string | null)[]): string {
  const index = allClubs.indexOf(club)
  return SERIES[(index < 0 ? 0 : index) % MAX_SERIES]
}

export function metricOf(meta: Metadata | null, key: string) {
  return meta?.metrics.find((m) => m.key === key)
}

export function unitOf(meta: Metadata | null, key: string): string {
  return meta?.units[key] ?? ''
}

/** Format a metric value at its own precision. Missing reads as an em dash. */
export function fmt(
  value: number | null | undefined,
  meta: Metadata | null,
  key: string,
): string {
  if (value === null || value === undefined) return '—'
  const decimals = metricOf(meta, key)?.decimals ?? 1
  return value.toFixed(decimals)
}

export function fmtNum(value: number | null | undefined, decimals = 1): string {
  if (value === null || value === undefined) return '—'
  return value.toFixed(decimals)
}

/** "162.0 (IQR 6.2)" — the spread always travels with the centre. */
export function fmtSummary(
  summary: Summary | undefined,
  meta: Metadata | null,
  key: string,
): string {
  if (!summary || summary.median === null) return '—'
  return `${fmt(summary.median, meta, key)}`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10)
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (!seconds) return '—'
  const minutes = Math.round(seconds / 60)
  return `${minutes} min`
}

export function fmtTimestamp(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const total = Math.floor(seconds)
  const mm = String(Math.floor(total / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

/**
 * Split a flag into its key and its subject.
 *
 * Flags are stored as `key` or `key:subject` (e.g. `out_of_range:ball_speed`)
 * so the review UI can name the specific failure rather than saying only
 * "needs review".
 */
export function splitFlag(flag: string): { key: string; subject: string | null } {
  const index = flag.indexOf(':')
  if (index < 0) return { key: flag, subject: null }
  return { key: flag.slice(0, index), subject: flag.slice(index + 1) }
}

export function describeFlag(flag: string, meta: Metadata | null): string {
  const { key, subject } = splitFlag(flag)
  const base = meta?.flag_descriptions[key] ?? key.replace(/_/g, ' ')
  if (!subject) return base
  const pretty = subject
    .split(',')
    .map((s) => metricOf(meta, s)?.label ?? s.replace(/_/g, ' '))
    .join(', ')
  return `${base}: ${pretty}`
}

export const STAT_KEYS = [
  'carry',
  'total',
  'ball_speed',
  'club_speed',
  'smash_factor',
  'apex',
  'spin_rate',
  'spin_axis',
  'face_angle',
  'club_path',
  'face_to_path',
  'launch_angle',
] as const

/** Editable subset: face_to_path is derived and never written. */
export const EDITABLE_STAT_KEYS = STAT_KEYS.filter((k) => k !== 'face_to_path')
