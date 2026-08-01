import { useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import { api } from '../api'
import { useMeta } from '../meta'
import { STAT_KEYS, fmt, fmtDate, metricOf, seriesColor } from '../format'
import type { Trend as TrendData } from '../types'
import { Card, Empty, ErrorBanner, SampleSize } from '../components/common'
import { AXIS, GRID_STROKE, Legend, TipBox } from '../components/charts'

/**
 * Spec section 7, session-over-session view.
 *
 *   "metric trend by club, with per-session shot counts visible so
 *    single-session noise is obvious."
 *
 *   "Do not surface a trend line until a club has at least 30 confirmed shots
 *    across at least 3 sessions."
 *
 * The gate governs the *line*, not the data: a club that has not earned one
 * still shows its points and its sample sizes, with the shortfall stated. That
 * is the honest reading — hiding the data would be a different claim than
 * declining to draw a trend through it.
 */
export function Trend() {
  const meta = useMeta()
  const [metric, setMetric] = useState('carry')
  const [data, setData] = useState<TrendData | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    setData(null)
    api.trend(metric).then(setData).catch(setError)
  }, [metric])

  const allClubs = useMemo(() => data?.clubs.map((c) => c.club) ?? [], [data])
  const eligible = data?.clubs.filter((c) => c.trend_eligible) ?? []
  const gated = data?.clubs.filter((c) => !c.trend_eligible) ?? []

  return (
    <>
      <div className="page-head">
        <h1>Session over session</h1>
        <div className="row">
          <label htmlFor="metric">Metric</label>
          <select id="metric" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {STAT_KEYS.map((key) => (
              <option key={key} value={key}>
                {metricOf(meta, key)?.label ?? key}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="page-note">
        A trend line appears only once a club has at least{' '}
        {data?.clubs[0]?.trend_gate.min_shots ?? 30} confirmed shots across at least{' '}
        {data?.clubs[0]?.trend_gate.min_sessions ?? 3} sessions. Below that, the points
        are shown without a line through them — three shots on a Tuesday is not a
        trend. Per-session shot counts are on every point.
      </p>

      <ErrorBanner error={error} />

      {!data ? (
        <Empty>Loading…</Empty>
      ) : data.clubs.length === 0 ? (
        <Card title="No confirmed shots">
          <Empty>Confirm a session and its shots will appear here.</Empty>
        </Card>
      ) : (
        <>
          {eligible.length > 0 && (
            <Card
              title={`${data.label} trend`}
              note={`${data.unit} · median per session · clubs that have earned a trend line`}
            >
              <Legend
                items={eligible.map((club) => ({
                  label: club.club ?? 'no club',
                  color: seriesColor(club.club, allClubs),
                }))}
              />
              <TrendLines data={data} clubs={eligible} allClubs={allClubs} />
            </Card>
          )}

          {gated.length > 0 && (
            <Card
              title="Not enough data for a trend"
              note="Points only. The line is withheld until the gate is cleared."
            >
              <div className="chart-grid">
                {gated.map((club) => (
                  <GatedFacet
                    key={club.club ?? 'none'}
                    club={club}
                    data={data}
                    allClubs={allClubs}
                  />
                ))}
              </div>
            </Card>
          )}

          <Card title="The numbers" note="The table view behind every chart on this page.">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Club</th>
                    <th>Session</th>
                    <th className="num">Shots</th>
                    <th className="num">
                      Median {data.label} <span className="unit">{data.unit}</span>
                    </th>
                    <th className="num">IQR</th>
                    <th>Trend</th>
                  </tr>
                </thead>
                <tbody>
                  {data.clubs.flatMap((club) =>
                    club.points.map((point) => (
                      <tr key={`${club.club}-${point.session_id}`}>
                        <td>{club.club ?? <span className="muted">no club</span>}</td>
                        <td>{fmtDate(point.recorded_at)}</td>
                        <td className="num">{point.shot_count}</td>
                        <td className="num">{fmt(point.median, meta, data.metric)}</td>
                        <td className="num">{fmt(point.iqr, meta, data.metric)}</td>
                        <td>
                          {club.trend_eligible ? (
                            <span className="badge ok">eligible</span>
                          ) : (
                            <span className="badge">gated</span>
                          )}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  )
}

function TrendLines({
  data,
  clubs,
  allClubs,
}: {
  data: TrendData
  clubs: TrendData['clubs']
  allClubs: (string | null)[]
}) {
  const meta = useMeta()

  // Merge every club's points onto a shared session-date axis.
  const dates = Array.from(
    new Set(clubs.flatMap((c) => c.points.map((p) => p.session_date ?? ''))),
  ).sort()

  const rows = dates.map((date) => {
    const row: Record<string, string | number | null> = { date }
    for (const club of clubs) {
      const point = club.points.find((p) => p.session_date === date)
      const key = club.club ?? 'no club'
      row[key] = point?.median ?? null
      row[`${key}__n`] = point?.shot_count ?? 0
    }
    return row
  })

  return (
    <div style={{ width: '100%', height: 300 }}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 66, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={GRID_STROKE} vertical={false} />
          <XAxis dataKey="date" {...AXIS} axisLine={{ stroke: GRID_STROKE }} />
          <YAxis
            {...AXIS}
            axisLine={false}
            width={46}
            domain={['auto', 'auto']}
            label={{
              value: data.unit,
              angle: -90,
              position: 'insideLeft',
              style: { fill: 'var(--text-muted)', fontSize: 11 },
            }}
          />
          <Tooltip
            cursor={{ stroke: 'var(--border-strong)', strokeWidth: 1 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null
              return (
                <TipBox
                  title={String(label)}
                  rows={payload.flatMap((entry) => {
                    const key = String(entry.name)
                    const row = entry.payload as Record<string, number>
                    if (entry.value === null || entry.value === undefined) return []
                    return [
                      [
                        key,
                        `${fmt(Number(entry.value), meta, data.metric)} ${data.unit} (n=${
                          row[`${key}__n`] ?? 0
                        })`,
                      ] as [string, string],
                    ]
                  })}
                />
              )
            }}
          />
          {clubs.map((club) => {
            const key = club.club ?? 'no club'
            return (
              <Line
                key={key}
                // Straight segments, not a spline: monotone interpolation
                // between three session medians draws a peak above the highest
                // one, which is a claim the data does not make.
                type="linear"
                dataKey={key}
                name={key}
                stroke={seriesColor(club.club, allClubs)}
                strokeWidth={2}
                // Explicit fill: without it the marker inherits a surface-ish
                // fill and vanishes against the light background. The 2px
                // surface ring is the spacer that keeps overlapping markers
                // readable.
                dot={{
                  r: 4,
                  fill: seriesColor(club.club, allClubs),
                  strokeWidth: 2,
                  stroke: 'var(--surface-1)',
                }}
                activeDot={{
                  r: 6,
                  fill: seriesColor(club.club, allClubs),
                  strokeWidth: 2,
                  stroke: 'var(--surface-1)',
                }}
                connectNulls
                isAnimationActive={false}
              >
                {/* Direct label at the line end: identity never rests on
                    colour alone, which the light-mode palette requires. */}
                <LabelList
                  dataKey={key}
                  content={(props) => {
                    const { index, x, y, value } = props as {
                      index?: number
                      x?: number
                      y?: number
                      value?: number | null
                    }
                    if (index !== rows.length - 1) return null
                    if (value === null || value === undefined) return null
                    return (
                      <text
                        x={(x ?? 0) + 8}
                        y={(y ?? 0) + 4}
                        fill="var(--text-secondary)"
                        fontSize={11}
                      >
                        {key}
                      </text>
                    )
                  }}
                />
              </Line>
            )
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function GatedFacet({
  club,
  data,
  allClubs,
}: {
  club: TrendData['clubs'][number]
  data: TrendData
  allClubs: (string | null)[]
}) {
  const meta = useMeta()
  const points = club.points.map((p, i) => ({
    x: i,
    y: p.median,
    date: p.session_date,
    n: p.shot_count,
  }))
  const shortfall: string[] = []
  if (club.trend_gate.shots_short_by > 0)
    shortfall.push(`${club.trend_gate.shots_short_by} more shots`)
  if (club.trend_gate.sessions_short_by > 0)
    shortfall.push(
      `${club.trend_gate.sessions_short_by} more session${
        club.trend_gate.sessions_short_by > 1 ? 's' : ''
      }`,
    )

  return (
    <div className="facet">
      <div className="facet-head">
        <span className="facet-title">{club.club ?? 'no club'}</span>
        <SampleSize n={club.shot_count} />
      </div>
      <div style={{ width: '100%', height: 150 }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 10, bottom: 4, left: 0 }}>
            <CartesianGrid stroke={GRID_STROKE} vertical={false} />
            <XAxis
              type="number"
              dataKey="x"
              {...AXIS}
              axisLine={{ stroke: GRID_STROKE }}
              ticks={points.map((p) => p.x)}
              tickFormatter={(v: number) => points[v]?.date?.slice(5) ?? ''}
              domain={[-0.4, points.length - 0.6]}
            />
            {/* Auto domain, not zero-based: these are session medians being
                compared to each other, so the interesting range is where the
                points actually sit. */}
            <YAxis
              type="number"
              dataKey="y"
              domain={['auto', 'auto']}
              {...AXIS}
              axisLine={false}
              width={40}
            />
            <ZAxis range={[70, 70]} />
            <Tooltip
              cursor={{ stroke: 'var(--border-strong)' }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const row = payload[0].payload as (typeof points)[number]
                return (
                  <TipBox
                    title={row.date ?? ''}
                    rows={[
                      [
                        `median ${data.label.toLowerCase()}`,
                        `${fmt(row.y, meta, data.metric)} ${data.unit}`,
                      ],
                      ['shots', String(row.n)],
                    ]}
                  />
                )
              }}
            />
            <Scatter
              data={points}
              fill={seriesColor(club.club, allClubs)}
              isAnimationActive={false}
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div className="gate" style={{ margin: '0 0 10px' }}>
        No trend line yet — needs {shortfall.join(' and ')}.
      </div>
    </div>
  )
}
