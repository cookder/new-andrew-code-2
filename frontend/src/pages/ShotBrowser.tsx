import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useMeta } from '../meta'
import { STAT_KEYS, fmt, fmtDate, metricOf } from '../format'
import type { BrowseResult, Facets } from '../types'
import { Card, Empty, ErrorBanner, Field } from '../components/common'

/**
 * Spec section 7: "Shot browser: filter by any field, click through to clip."
 *
 * Every row carries its clip and its source frame, so any number on screen is
 * one click from the swing that produced it.
 */
export function ShotBrowser() {
  const meta = useMeta()
  const [facets, setFacets] = useState<Facets | null>(null)
  const [result, setResult] = useState<BrowseResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [selected, setSelected] = useState<BrowseResult['shots'][number] | null>(null)

  const [filters, setFilters] = useState({
    club: '',
    strike_location: '',
    tag: '',
    text: '',
    carry_min: '',
    carry_max: '',
    smash_factor_min: '',
    confirmed_only: true,
    flagged: '',
  })

  useEffect(() => {
    api.facets().then(setFacets).catch(setError)
  }, [])

  useEffect(() => {
    const params: Record<string, string | number | boolean | null> = {
      confirmed_only: filters.confirmed_only,
    }
    if (filters.club) params.club = filters.club
    if (filters.strike_location) params.strike_location = filters.strike_location
    if (filters.tag) params.tag = filters.tag
    if (filters.text) params.text = filters.text
    if (filters.carry_min) params.carry_min = Number(filters.carry_min)
    if (filters.carry_max) params.carry_max = Number(filters.carry_max)
    if (filters.smash_factor_min) params.smash_factor_min = Number(filters.smash_factor_min)
    if (filters.flagged) params.flagged = filters.flagged === 'yes'

    api.browse(params).then(setResult).catch(setError)
  }, [filters])

  function set<K extends keyof typeof filters>(key: K, value: (typeof filters)[K]) {
    setFilters((current) => ({ ...current, [key]: value }))
  }

  return (
    <>
      <div className="page-head">
        <h1>Shot browser</h1>
        {result && (
          <span className="muted">
            {result.total} shot{result.total === 1 ? '' : 's'}
          </span>
        )}
      </div>
      <p className="page-note">
        Filter on any field, then click a row to watch the swing behind it.
      </p>

      <ErrorBanner error={error} />

      <Card title="Filters">
        <div className="row">
          <Field label="Club">
            <select value={filters.club} onChange={(e) => set('club', e.target.value)}>
              <option value="">Any</option>
              {(facets?.clubs ?? []).map((club) => (
                <option key={club} value={club}>
                  {club}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Strike">
            <select
              value={filters.strike_location}
              onChange={(e) => set('strike_location', e.target.value)}
            >
              <option value="">Any</option>
              {(facets?.strike_locations ?? []).map((loc) => (
                <option key={loc} value={loc}>
                  {loc}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Tag">
            <select value={filters.tag} onChange={(e) => set('tag', e.target.value)}>
              <option value="">Any</option>
              {(facets?.tags ?? []).map((tag) => (
                <option key={tag} value={tag}>
                  {tag}
                </option>
              ))}
            </select>
          </Field>
          <Field label={`Carry ≥ (${meta?.units.carry ?? ''})`}>
            <input
              type="number"
              style={{ width: 82 }}
              value={filters.carry_min}
              onChange={(e) => set('carry_min', e.target.value)}
            />
          </Field>
          <Field label={`Carry ≤ (${meta?.units.carry ?? ''})`}>
            <input
              type="number"
              style={{ width: 82 }}
              value={filters.carry_max}
              onChange={(e) => set('carry_max', e.target.value)}
            />
          </Field>
          <Field label="Smash ≥">
            <input
              type="number"
              step="0.01"
              style={{ width: 82 }}
              value={filters.smash_factor_min}
              onChange={(e) => set('smash_factor_min', e.target.value)}
            />
          </Field>
          <Field label="Spoken words">
            <input
              value={filters.text}
              placeholder="hooked, thin…"
              onChange={(e) => set('text', e.target.value)}
            />
          </Field>
          <Field label="Flagged">
            <select value={filters.flagged} onChange={(e) => set('flagged', e.target.value)}>
              <option value="">Any</option>
              <option value="yes">Flagged only</option>
              <option value="no">Clean only</option>
            </select>
          </Field>
          <Field label="Dataset">
            <select
              value={filters.confirmed_only ? 'confirmed' : 'all'}
              onChange={(e) => set('confirmed_only', e.target.value === 'confirmed')}
            >
              <option value="confirmed">Confirmed only</option>
              <option value="all">Include unreviewed</option>
            </select>
          </Field>
        </div>
      </Card>

      {selected && (
        <Card
          title={`Shot ${selected.shot_index} · ${selected.club ?? 'no club'}`}
          actions={<button onClick={() => setSelected(null)}>Close</button>}
        >
          <div className="detail">
            <div>
              <div className="stat-row" style={{ marginBottom: 12 }}>
                {(['carry', 'ball_speed', 'smash_factor', 'apex'] as const).map((key) => (
                  <div className="stat" key={key}>
                    <div className="stat-label">
                      {metricOf(meta, key)?.label} ({meta?.units[key]})
                    </div>
                    <div className="stat-value">{fmt(selected[key], meta, key)}</div>
                  </div>
                ))}
              </div>
              <p style={{ margin: '0 0 6px' }}>
                <strong>Strike:</strong> {selected.strike_location ?? '—'}
              </p>
              <p style={{ margin: '0 0 6px' }}>
                <strong>Flight:</strong>{' '}
                {selected.ball_flight ?? <span className="muted">not stated</span>}
              </p>
              <p style={{ margin: 0 }}>
                <strong>Felt like:</strong>{' '}
                {selected.self_assessment ?? <span className="muted">not stated</span>}
              </p>
            </div>
            <div className="detail-media">
              <ShotMedia shotId={selected.id} />
            </div>
          </div>
        </Card>
      )}

      {!result ? (
        <Empty>Loading…</Empty>
      ) : result.shots.length === 0 ? (
        <Card>
          <Empty>No shots match these filters.</Empty>
        </Card>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th className="num">#</th>
                <th>Club</th>
                <th>Strike</th>
                {STAT_KEYS.map((key) => (
                  <th key={key} className="num">
                    {metricOf(meta, key)?.label ?? key}
                    <br />
                    <span className="unit">{meta?.units[key]}</span>
                  </th>
                ))}
                <th>Spoken</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {result.shots.map((shot) => (
                <tr
                  key={shot.id}
                  onClick={() => setSelected(shot)}
                  style={{ cursor: 'pointer' }}
                >
                  <td>{fmtDate(shot.recorded_at)}</td>
                  <td className="num">{shot.shot_index}</td>
                  <td>{shot.club ?? <span className="muted">—</span>}</td>
                  <td>
                    {shot.strike_location ?? <span className="muted">—</span>}
                  </td>
                  {STAT_KEYS.map((key) => (
                    <td key={key} className="num">
                      {fmt(shot[key], meta, key)}
                    </td>
                  ))}
                  <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {shot.ball_flight ?? <span className="muted">—</span>}
                  </td>
                  <td>
                    <Link
                      to={`/sessions/${shot.session_id}`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      Session
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function ShotMedia({ shotId }: { shotId: number }) {
  const [artifacts, setArtifacts] = useState<{ id: number; kind: string }[] | null>(null)

  useEffect(() => {
    fetch(`/api/shots/${shotId}`)
      .then((r) => r.json())
      .then((shot) => setArtifacts(shot.artifacts ?? []))
      .catch(() => setArtifacts([]))
  }, [shotId])

  if (!artifacts) return <div className="gate">Loading media…</div>

  const clip = artifacts.find((a) => a.kind === 'clip')
  const frame = artifacts.find((a) => a.kind === 'stat_frame')

  return (
    <>
      {clip ? (
        <video src={api.artifactUrl(clip.id)} controls preload="metadata" />
      ) : (
        <div className="gate">No clip stored for this shot.</div>
      )}
      {frame && <img src={api.artifactUrl(frame.id)} alt="Stat panel source frame" />}
    </>
  )
}
