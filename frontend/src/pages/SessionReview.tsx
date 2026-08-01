import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../api'
import { useMeta } from '../meta'
import {
  EDITABLE_STAT_KEYS,
  STAT_KEYS,
  fmt,
  fmtDate,
  fmtTimestamp,
  metricOf,
} from '../format'
import type { SessionDetail, Shot } from '../types'
import {
  Card,
  EditableCell,
  Empty,
  ErrorBanner,
  Field,
  FlagList,
  Select,
} from '../components/common'

/**
 * Spec section 6 -- the mandatory gate between extraction and the confirmed
 * dataset.
 *
 *   "OCR will misread and Whisper will mishear."
 *
 * The rules this screen implements, in the spec's own terms:
 *   * Rows passing all validation default to confirmed, collapsed.
 *   * Rows with any flag default to expanded, with the specific failure named.
 *   * Every field inline-editable.
 *   * Bulk club reassignment for a contiguous range of shots.
 *   * Confirming a session makes its shots eligible for analysis.
 */
export function SessionReview() {
  const { id } = useParams()
  const sessionId = Number(id)
  const meta = useMeta()

  const [session, setSession] = useState<SessionDetail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const detail = await api.session(sessionId)
      setSession(detail)
      // Flagged rows open by default; clean rows stay collapsed. Applied on
      // first load only, so a reviewer's manual open/close survives a refresh
      // triggered by their own edit.
      setExpanded((current) =>
        current.size
          ? current
          : new Set(detail.shots.filter((s) => s.flags.length).map((s) => s.id)),
      )
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [sessionId])

  useEffect(() => {
    void load()
  }, [load])

  const patchShot = useCallback(
    async (shotId: number, body: Partial<Shot>) => {
      try {
        const updated = await api.updateShot(shotId, body)
        setSession((current) =>
          current
            ? {
                ...current,
                shots: current.shots.map((s) => (s.id === shotId ? updated : s)),
              }
            : current,
        )
        setError(null)
      } catch (err) {
        setError(err)
      }
    },
    [],
  )

  const shots = session?.shots ?? []
  const flaggedCount = useMemo(() => shots.filter((s) => s.flags.length).length, [shots])
  const confirmed = session?.status === 'confirmed'

  async function confirmSession() {
    setBusy(true)
    try {
      await api.confirmSession(sessionId)
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function unconfirmSession() {
    setBusy(true)
    try {
      await api.unconfirmSession(sessionId)
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  if (error && !session) return <ErrorBanner error={error} />
  if (!session) return <Empty>Loading…</Empty>

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{session.source_filename ?? `Session ${session.id}`}</h1>
          <div className="muted" style={{ fontSize: 13 }}>
            {fmtDate(session.recorded_at)} · {shots.length} shots ·{' '}
            {flaggedCount > 0 ? (
              <strong>{flaggedCount} need review</strong>
            ) : (
              'all rows pass validation'
            )}
          </div>
        </div>
        <div className="row">
          <Link to="/sessions">All sessions</Link>
          {confirmed ? (
            <button onClick={unconfirmSession} disabled={busy}>
              Reopen for review
            </button>
          ) : (
            <button className="primary" onClick={confirmSession} disabled={busy}>
              Confirm session
            </button>
          )}
        </div>
      </div>

      <p className="page-note">
        Nothing reaches the analysis views until this session is confirmed. Rows that
        fail a validation rule are expanded with the reason named; rows that pass are
        collapsed. Every field is editable in place — click a value and type.
        {confirmed && ' This session is confirmed and its shots are in the dataset.'}
      </p>

      <ErrorBanner error={error} />

      <BulkClubBar sessionId={sessionId} shots={shots} onDone={load} />

      {session.anomalies.length > 0 && <AnomalyList anomalies={session.anomalies} />}

      {shots.length === 0 ? (
        <Card title="No shots yet">
          <Empty>
            This session has no shots. Add them by hand below, or run the pipeline.
          </Empty>
        </Card>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th />
                <th>#</th>
                <th>Club</th>
                {STAT_KEYS.map((key) => (
                  <th key={key} className="num">
                    {metricOf(meta, key)?.label ?? key}
                    <br />
                    <span className="unit">{meta?.units[key]}</span>
                  </th>
                ))}
                <th>Strike</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {shots.map((shot) => (
                <ShotRows
                  key={shot.id}
                  shot={shot}
                  expanded={expanded.has(shot.id)}
                  onToggle={() =>
                    setExpanded((current) => {
                      const next = new Set(current)
                      if (next.has(shot.id)) next.delete(shot.id)
                      else next.add(shot.id)
                      return next
                    })
                  }
                  onPatch={patchShot}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ManualEntry sessionId={sessionId} onAdded={load} />
    </>
  )
}

function ShotRows({
  shot,
  expanded,
  onToggle,
  onPatch,
}: {
  shot: Shot
  expanded: boolean
  onToggle: () => void
  onPatch: (id: number, body: Partial<Shot>) => void
}) {
  const meta = useMeta()
  const flagged = shot.flags.length > 0
  const frame = shot.artifacts.find((a) => a.kind === 'stat_frame')
  const clip = shot.artifacts.find((a) => a.kind === 'clip')

  const numeric = (raw: string | null) => (raw === null ? null : Number(raw))

  return (
    <>
      <tr className={`review-row ${flagged ? 'flagged' : ''}`}>
        <td className="expander" onClick={onToggle} title={expanded ? 'Collapse' : 'Expand'}>
          {expanded ? '▾' : '▸'}
        </td>
        <td className="mono">{shot.shot_index}</td>
        <td>
          <Select
            value={shot.club}
            options={meta?.clubs ?? []}
            onChange={(club) => onPatch(shot.id, { club })}
          />
        </td>
        {STAT_KEYS.map((key) =>
          key === 'face_to_path' ? (
            // Derived from face angle - club path. Read-only by construction:
            // the database computes it and refuses a written value.
            <td key={key} className="num" title="Derived: face angle − club path">
              <span className="muted">{fmt(shot[key], meta, key)}</span>
            </td>
          ) : (
            <td key={key} className="num">
              <EditableCell
                value={shot[key as keyof Shot] as number | null}
                onCommit={(raw) => onPatch(shot.id, { [key]: numeric(raw) } as Partial<Shot>)}
              />
            </td>
          ),
        )}
        <td>
          <Select
            value={shot.strike_location}
            options={meta?.strike_locations ?? []}
            onChange={(value) =>
              onPatch(shot.id, { strike_location: value as Shot['strike_location'] })
            }
          />
        </td>
        <td>
          {flagged ? (
            <span className="badge flag">{shot.flags.length} issue{shot.flags.length > 1 ? 's' : ''}</span>
          ) : shot.confidence === 'high' ? (
            <span className="badge ok">confirmed</span>
          ) : (
            <span className="badge">clean</span>
          )}
        </td>
      </tr>

      {expanded && (
        <tr>
          <td className="detail-cell" colSpan={STAT_KEYS.length + 5}>
            <div className="detail">
              <div>
                <FlagList flags={shot.flags} />

                <div className="detail-fields">
                  <Field label="Club source">
                    <Select
                      value={shot.club_source}
                      options={meta?.club_sources ?? []}
                      onChange={(value) =>
                        onPatch(shot.id, { club_source: value as Shot['club_source'] })
                      }
                    />
                  </Field>
                  <Field label="Ball flight (spoken)">
                    <EditableCell
                      type="text"
                      align="left"
                      value={shot.ball_flight}
                      placeholder="—"
                      onCommit={(value) => onPatch(shot.id, { ball_flight: value })}
                    />
                  </Field>
                  <Field label="Self assessment (spoken)">
                    <EditableCell
                      type="text"
                      align="left"
                      value={shot.self_assessment}
                      placeholder="—"
                      onCommit={(value) => onPatch(shot.id, { self_assessment: value })}
                    />
                  </Field>
                  <Field label="Tags">
                    <EditableCell
                      type="text"
                      align="left"
                      value={shot.tags.join(', ')}
                      placeholder="thin, pull"
                      onCommit={(value) =>
                        onPatch(shot.id, {
                          tags: (value ?? '')
                            .split(',')
                            .map((t) => t.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  </Field>
                  <Field label="Impact">
                    <span className="mono">{fmtTimestamp(shot.impact_ts)}</span>
                  </Field>
                  <Field label="Panel refresh">
                    <span className="mono">{fmtTimestamp(shot.panel_change_ts)}</span>
                  </Field>
                </div>
              </div>

              <div className="detail-media">
                {frame ? (
                  <figure style={{ margin: 0 }}>
                    <img src={api.artifactUrl(frame.id)} alt="Stat panel source frame" />
                    <figcaption className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                      Source frame the numbers were read from
                    </figcaption>
                  </figure>
                ) : (
                  <div className="gate">No stat frame stored for this shot.</div>
                )}
                {clip ? (
                  <figure style={{ margin: 0 }}>
                    <video src={api.artifactUrl(clip.id)} controls preload="metadata" />
                    <figcaption className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                      The swing that produced these numbers
                    </figcaption>
                  </figure>
                ) : (
                  <div className="gate">No clip stored for this shot.</div>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/**
 * Bulk club reassignment over a contiguous range.
 *
 * This is the one action that repairs an unannounced club switch across a whole
 * block, which is exactly the failure the ball-speed guard flags.
 */
function BulkClubBar({
  sessionId,
  shots,
  onDone,
}: {
  sessionId: number
  shots: Shot[]
  onDone: () => void
}) {
  const meta = useMeta()
  const [club, setClub] = useState<string | null>(null)
  const [from, setFrom] = useState(1)
  const [to, setTo] = useState(shots.length || 1)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    setTo(shots.length || 1)
  }, [shots.length])

  const suspects = shots.filter((s) =>
    s.flags.some((f) => f.startsWith('suspected_club_change')),
  )

  async function apply() {
    if (!club) return
    setBusy(true)
    try {
      await api.bulkClub(sessionId, club, from, to)
      setError(null)
      onDone()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title="Bulk club reassignment"
      note="Fixes a whole block in one action when the club changed without being announced."
    >
      <ErrorBanner error={error} />
      {suspects.length > 0 && (
        <p className="page-note" style={{ marginBottom: 12 }}>
          Ball speed jumps at shot{suspects.length > 1 ? 's' : ''}{' '}
          <strong>{suspects.map((s) => s.shot_index).join(', ')}</strong> — the club may
          have changed there without being said out loud.{' '}
          <button
            style={{ padding: '2px 8px' }}
            onClick={() => {
              setFrom(suspects[0].shot_index)
              setTo(shots.length)
            }}
          >
            Select from shot {suspects[0].shot_index} to the end
          </button>
        </p>
      )}
      <div className="row">
        <Field label="Club">
          <Select
            value={club}
            options={meta?.clubs ?? []}
            onChange={setClub}
            placeholder="Pick a club"
          />
        </Field>
        <Field label="From shot">
          <input
            type="number"
            min={1}
            max={Math.max(shots.length, 1)}
            value={from}
            style={{ width: 70 }}
            onChange={(e) => setFrom(Number(e.target.value))}
          />
        </Field>
        <Field label="To shot">
          <input
            type="number"
            min={1}
            max={Math.max(shots.length, 1)}
            value={to}
            style={{ width: 70 }}
            onChange={(e) => setTo(Number(e.target.value))}
          />
        </Field>
        <button onClick={apply} disabled={!club || busy} style={{ alignSelf: 'flex-end' }}>
          Reassign {Math.abs(to - from) + 1} shot{Math.abs(to - from) === 0 ? '' : 's'}
        </button>
      </div>
    </Card>
  )
}

function AnomalyList({ anomalies }: { anomalies: SessionDetail['anomalies'] }) {
  return (
    <Card
      title={`${anomalies.length} detection anomal${anomalies.length === 1 ? 'y' : 'ies'}`}
      note="Signals that never paired into a shot. Logged rather than dropped."
    >
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>At</th>
              <th>Kind</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {anomalies.map((anomaly) => (
              <tr key={anomaly.id}>
                <td className="mono">{fmtTimestamp(anomaly.ts)}</td>
                <td>{anomaly.kind.replace(/_/g, ' ')}</td>
                <td style={{ whiteSpace: 'normal' }}>{anomaly.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/**
 * Manual entry.
 *
 * Phase 1 of the build order is hand-entered data: the schema, the review UI,
 * and every analysis view are meant to work before any video is processed.
 */
function ManualEntry({ sessionId, onAdded }: { sessionId: number; onAdded: () => void }) {
  const meta = useMeta()
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [club, setClub] = useState<string | null>(null)
  const [strike, setStrike] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function add() {
    setBusy(true)
    try {
      const body: Record<string, unknown> = { club, strike_location: strike }
      for (const key of EDITABLE_STAT_KEYS) {
        const raw = draft[key]
        body[key] = raw === undefined || raw === '' ? null : Number(raw)
      }
      await api.createShot(sessionId, body as never)
      setDraft({})
      setError(null)
      onAdded()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title="Add a shot by hand"
      note="Face to path is not listed: it is derived from face angle minus club path."
    >
      <ErrorBanner error={error} />
      <div className="detail-fields" style={{ marginBottom: 12 }}>
        <Field label="Club">
          <Select value={club} options={meta?.clubs ?? []} onChange={setClub} />
        </Field>
        <Field label="Strike location">
          <Select
            value={strike}
            options={meta?.strike_locations ?? []}
            onChange={setStrike}
          />
        </Field>
        {EDITABLE_STAT_KEYS.map((key) => (
          <Field key={key} label={`${metricOf(meta, key)?.label ?? key} (${meta?.units[key] ?? ''})`}>
            <input
              type="number"
              step="any"
              value={draft[key] ?? ''}
              onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
            />
          </Field>
        ))}
      </div>
      <button className="primary" onClick={add} disabled={busy}>
        Add shot
      </button>
    </Card>
  )
}
