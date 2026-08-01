import { useEffect, useState } from 'react'
import {
  CartesianGrid,
  ReferenceLine,
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
import { fmt } from '../format'
import type { Dispersion as DispersionData, DispersionPoint } from '../types'
import { Card, Empty, ErrorBanner, SampleSize } from '../components/common'
import { AXIS, GRID_STROKE, TipBox } from '../components/charts'

/**
 * Spec section 7, dispersion view: face angle against club path, by strike
 * location.
 *
 * Faceted rather than colour-coded. Six strike locations on one scatter would
 * need six hues told apart pairwise at small mark sizes, which no eight-slot
 * palette clears for colourblind readers — and the reader's real question here
 * ("does my heel strike come with an open face?") is answered better by putting
 * each location in its own panel than by asking them to pick one colour out of
 * six overlapping clouds.
 *
 * Each panel shows its own strikes in the accent hue over the whole population
 * in grey, so every panel is read against the same backdrop.
 *
 * The diagonal is face-to-path: distance from it is the face-to-path number
 * that actually shapes the ball, which is why the reference line is drawn.
 */
export function Dispersion() {
  const meta = useMeta()
  const [data, setData] = useState<DispersionData | null>(null)
  const [club, setClub] = useState<string>('')
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    setData(null)
    api.dispersion(club || undefined).then(setData).catch(setError)
  }, [club])

  const locations = meta?.strike_locations ?? []
  const points = data?.points ?? []

  return (
    <>
      <div className="page-head">
        <h1>Dispersion</h1>
        <div className="row">
          <label htmlFor="club">Club</label>
          <select id="club" value={club} onChange={(e) => setClub(e.target.value)}>
            <option value="">All clubs</option>
            {(data?.clubs ?? []).map((name) => (
              <option key={name ?? ''} value={name ?? ''}>
                {name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="page-note">
        Face angle against club path, one panel per spoken strike location. Points on
        the dashed diagonal have a face square to the path; distance from it is
        face-to-path, which is what actually curves the ball. Grey points are every
        shot in the current filter, so each panel is read against the same backdrop.
      </p>

      <ErrorBanner error={error} />

      {!data ? (
        <Empty>Loading…</Empty>
      ) : points.length === 0 ? (
        <Card title="No confirmed shots with both angles">
          <Empty>
            Face angle and club path are both needed to place a shot on this plot.
          </Empty>
        </Card>
      ) : (
        <>
          <div className="chart-grid">
            {locations.map((location) => (
              <Facet
                key={location}
                title={location}
                context={points}
                highlighted={points.filter((p) => p.strike_location === location)}
                meta={meta}
              />
            ))}
            <Facet
              title="not stated"
              context={points}
              highlighted={points.filter((p) => p.strike_location === null)}
              meta={meta}
              muted
            />
          </div>

          <p className="page-note" style={{ marginTop: 16 }}>
            {data.point_count} shots plotted{club ? ` for the ${club}` : ' across all clubs'}.
          </p>
        </>
      )}
    </>
  )
}

function Facet({
  title,
  context,
  highlighted,
  meta,
  muted = false,
}: {
  title: string
  context: DispersionPoint[]
  highlighted: DispersionPoint[]
  meta: ReturnType<typeof useMeta>
  muted?: boolean
}) {
  if (highlighted.length === 0) {
    return (
      <div className="facet">
        <div className="facet-head">
          <span className="facet-title">{title}</span>
          <SampleSize n={0} />
        </div>
        <div className="gate" style={{ margin: '10px 0' }}>
          No shots recorded with this strike location.
        </div>
      </div>
    )
  }

  const bound =
    Math.ceil(
      Math.max(
        ...context.flatMap((p) => [Math.abs(p.face_angle), Math.abs(p.club_path)]),
        4,
      ),
    ) + 1

  return (
    <div className="facet">
      <div className="facet-head">
        <span className="facet-title">{title}</span>
        <SampleSize n={highlighted.length} />
      </div>
      <div style={{ width: '100%', height: 210 }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 6, right: 10, bottom: 18, left: 0 }}>
            <CartesianGrid stroke={GRID_STROKE} />
            <XAxis
              type="number"
              dataKey="club_path"
              domain={[-bound, bound]}
              {...AXIS}
              axisLine={{ stroke: GRID_STROKE }}
              label={{
                value: `club path (${meta?.units.club_path ?? ''})`,
                position: 'insideBottom',
                offset: -12,
                style: { fill: 'var(--text-muted)', fontSize: 11 },
              }}
            />
            <YAxis
              type="number"
              dataKey="face_angle"
              domain={[-bound, bound]}
              width={40}
              {...AXIS}
              axisLine={false}
              label={{
                value: 'face angle',
                angle: -90,
                position: 'insideLeft',
                style: { fill: 'var(--text-muted)', fontSize: 11 },
              }}
            />
            <ZAxis range={[46, 46]} />
            <ReferenceLine
              segment={[
                { x: -bound, y: -bound },
                { x: bound, y: bound },
              ]}
              stroke="var(--border-strong)"
              strokeDasharray="4 4"
            />
            <ReferenceLine x={0} stroke={GRID_STROKE} />
            <ReferenceLine y={0} stroke={GRID_STROKE} />
            <Tooltip
              cursor={{ strokeDasharray: '3 3', stroke: 'var(--border-strong)' }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const p = payload[0].payload as DispersionPoint
                return (
                  <TipBox
                    title={`${p.club ?? 'no club'} · ${p.strike_location ?? 'not stated'}`}
                    rows={[
                      ['face angle', `${fmt(p.face_angle, meta, 'face_angle')}°`],
                      ['club path', `${fmt(p.club_path, meta, 'club_path')}°`],
                      ['face to path', `${fmt(p.face_to_path, meta, 'face_to_path')}°`],
                      ['carry', fmt(p.carry, meta, 'carry')],
                    ]}
                  />
                )
              }}
            />
            {/* Context first so the highlighted marks sit on top. */}
            <Scatter
              data={context}
              fill="var(--neutral-mark)"
              fillOpacity={0.5}
              isAnimationActive={false}
            />
            <Scatter
              data={highlighted}
              fill={muted ? 'var(--text-muted)' : 'var(--series-1)'}
              stroke="var(--surface-1)"
              strokeWidth={2}
              isAnimationActive={false}
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
