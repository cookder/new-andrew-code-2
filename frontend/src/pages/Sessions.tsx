import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { fmtDate, fmtDuration } from '../format'
import type { Session } from '../types'
import { Card, Empty, ErrorBanner, Field } from '../components/common'

export function Sessions() {
  const [sessions, setSessions] = useState<Session[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  async function load() {
    try {
      setSessions(await api.sessions())
      setError(null)
    } catch (err) {
      setError(err)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <>
      <div className="page-head">
        <h1>Sessions</h1>
      </div>
      <p className="page-note">
        Every session passes through review before its shots reach the analysis views.
        The counts below are the work waiting in each one.
      </p>

      <ErrorBanner error={error} />

      {sessions === null ? (
        <Empty>Loading…</Empty>
      ) : sessions.length === 0 ? (
        <Card title="No sessions yet">
          <Empty>
            Create a session below and enter shots by hand, or drop an MP4 in the
            watched folder.
          </Empty>
        </Card>
      ) : (
        <div className="table-scroll" style={{ marginBottom: 16 }}>
          <table>
            <thead>
              <tr>
                <th>Recorded</th>
                <th>Source</th>
                <th>Status</th>
                <th className="num">Shots</th>
                <th className="num">Need review</th>
                <th className="num">Confirmed</th>
                <th className="num">Length</th>
                <th>Notes</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.id}>
                  <td>{fmtDate(session.recorded_at)}</td>
                  <td className="mono">{session.source_filename ?? '—'}</td>
                  <td>
                    <span
                      className={`badge ${session.status === 'confirmed' ? 'ok' : ''} ${
                        session.status === 'needs_calibration' || session.status === 'failed'
                          ? 'flag'
                          : ''
                      }`}
                    >
                      {session.status.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="num">{session.shot_count ?? 0}</td>
                  <td className="num">
                    {session.flagged_count ? (
                      <strong>{session.flagged_count}</strong>
                    ) : (
                      <span className="muted">0</span>
                    )}
                  </td>
                  <td className="num">{session.confirmed_count ?? 0}</td>
                  <td className="num">{fmtDuration(session.duration_s)}</td>
                  <td style={{ whiteSpace: 'normal', maxWidth: 280 }}>
                    {session.notes ?? <span className="muted">—</span>}
                  </td>
                  <td>
                    <Link to={`/sessions/${session.id}`}>Review</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <NewSession onCreated={load} />
    </>
  )
}

function NewSession({ onCreated }: { onCreated: () => void }) {
  const today = new Date().toISOString().slice(0, 10)
  const [recorded, setRecorded] = useState(today)
  const [filename, setFilename] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function create() {
    setBusy(true)
    try {
      await api.createSession({
        recorded_at: `${recorded}T17:00:00`,
        source_filename: filename || null,
        notes: notes || null,
        status: 'extracted',
      })
      setFilename('')
      setNotes('')
      setError(null)
      onCreated()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title="New session"
      note="For hand-entering a range session that was not recorded, or was recorded but not yet ingested."
    >
      <ErrorBanner error={error} />
      <div className="row">
        <Field label="Date">
          <input type="date" value={recorded} onChange={(e) => setRecorded(e.target.value)} />
        </Field>
        <Field label="Source filename (optional)">
          <input
            value={filename}
            placeholder="range-2026-07-01.mp4"
            onChange={(e) => setFilename(e.target.value)}
          />
        </Field>
        <Field label="Notes">
          <input
            value={notes}
            placeholder="What you were working on"
            style={{ minWidth: 240 }}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>
        <button className="primary" onClick={create} disabled={busy} style={{ alignSelf: 'flex-end' }}>
          Create
        </button>
      </div>
    </Card>
  )
}
