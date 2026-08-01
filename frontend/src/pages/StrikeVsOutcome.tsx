import { useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../api'
import { useMeta } from '../meta'
import { fmt, fmtNum } from '../format'
import type { StrikeClub, StrikeVsOutcome as StrikeData } from '../types'
import { Card, Empty, ErrorBanner } from '../components/common'
import { AXIS, GRID_STROKE, TipBox } from '../components/charts'

/**
 * Spec section 7 -- the core view.
 *
 *   "for each club, group by strike_location and compare smash factor, carry,
 *    and dispersion. This is the core view. It is what the whole system exists
 *    to produce."
 *
 * Strike location comes only from the golfer's voice; the simulator never
 * reports it. Joining it to the launch monitor numbers is the thing no
 * off-the-shelf product does, and it is the reason the audio pipeline exists.
 *
 * Form: one bar chart per club, one series (carry), so magnitude is the job and
 * a single sequential hue is the right encoding. Strike locations are the
 * categories along the axis, not colours — which keeps six classes legible and
 * avoids leaning on hue for identity.
 */
export function StrikeVsOutcome() {
  const meta = useMeta()
  const [data, setData] = useState<StrikeData | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    api.strikeVsOutcome().then(setData).catch(setError)
  }, [])

  return (
    <>
      <div className="page-head">
        <h1>Strike location vs outcome</h1>
      </div>
      <p className="page-note">
        What each part of the face actually costs you, per club. Strike location is
        spoken, not measured — the simulator does not report it — so this joins what
        you said about a shot to what the launch monitor recorded about it. Distance
        dispersion is the carry IQR; direction is approximated by the spin-axis IQR,
        since the panel reports no lateral offset.
      </p>

      <ErrorBanner error={error} />

      {!data ? (
        <Empty>Loading…</Empty>
      ) : data.clubs.length === 0 ? (
        <Card title="No confirmed shots">
          <Empty>Confirm a session and its shots will appear here.</Empty>
        </Card>
      ) : (
        data.clubs.map((club) => <ClubPanel key={club.club ?? 'none'} club={club} meta={meta} />)
      )}
    </>
  )
}

function ClubPanel({ club, meta }: { club: StrikeClub; meta: ReturnType<typeof useMeta> }) {
  const named = club.locations.filter((l) => l.strike_location !== null)
  const unattributed = club.locations.find((l) => l.strike_location === null)

  const chartData = named
    .filter((l) => l.carry.median !== null)
    .map((l) => ({
      strike: l.strike_location as string,
      carry: l.carry.median as number,
      smash: l.smash_factor.median,
      n: l.shot_count,
    }))

  const best = chartData.reduce(
    (max, row) => (row.carry > max ? row.carry : max),
    Number.NEGATIVE_INFINITY,
  )

  return (
    <Card
      title={club.club ?? 'No club assigned'}
      note={
        <>
          {club.shot_count} confirmed shots · {club.attributed_count} with a spoken
          strike location
          {unattributed ? ` · ${unattributed.shot_count} unattributed` : ''}
        </>
      }
    >
      {chartData.length === 0 ? (
        <Empty>No spoken strike locations recorded for this club yet.</Empty>
      ) : (
        <div style={{ width: '100%', height: 210, marginBottom: 14 }}>
          <ResponsiveContainer>
            <BarChart data={chartData} margin={{ top: 16, right: 12, bottom: 4, left: 4 }}>
              <CartesianGrid stroke={GRID_STROKE} vertical={false} />
              <XAxis dataKey="strike" {...AXIS} axisLine={{ stroke: GRID_STROKE }} />
              <YAxis
                {...AXIS}
                axisLine={false}
                width={44}
                label={{
                  value: `carry (${meta?.units.carry ?? ''})`,
                  angle: -90,
                  position: 'insideLeft',
                  style: { fill: 'var(--text-muted)', fontSize: 11 },
                }}
              />
              <Tooltip
                cursor={{ fill: 'var(--surface-2)' }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const row = payload[0].payload as (typeof chartData)[number]
                  return (
                    <TipBox
                      title={row.strike}
                      rows={[
                        ['median carry', `${fmt(row.carry, meta, 'carry')} ${meta?.units.carry}`],
                        ['median smash', fmt(row.smash, meta, 'smash_factor')],
                        ['shots', String(row.n)],
                      ]}
                    />
                  )
                }}
              />
              <Bar
                dataKey="carry"
                radius={[4, 4, 0, 0]}
                maxBarSize={58}
                isAnimationActive={false}
              >
                {/* Emphasis: the best-performing strike carries the accent hue,
                    the rest recede. One series, so no legend is needed. */}
                {chartData.map((row) => (
                  <Cell
                    key={row.strike}
                    fill={row.carry === best ? 'var(--seq-450)' : 'var(--seq-250)'}
                  />
                ))}
                <LabelList
                  dataKey="carry"
                  position="top"
                  formatter={(value) => (value == null ? '' : Number(value).toFixed(0))}
                  style={{ fill: 'var(--text-secondary)', fontSize: 11 }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Strike</th>
              <th className="num">Shots</th>
              <th className="num">
                Smash <span className="unit">ratio</span>
              </th>
              <th className="num">
                Carry <span className="unit">{meta?.units.carry}</span>
              </th>
              <th className="num">
                Ball speed <span className="unit">{meta?.units.ball_speed}</span>
              </th>
              <th className="num">
                Apex <span className="unit">{meta?.units.apex}</span>
              </th>
              <th className="num">
                Carry IQR <span className="unit">{meta?.units.carry}</span>
              </th>
              <th className="num">
                Spin axis IQR <span className="unit">{meta?.units.spin_axis}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {club.locations.map((loc) => (
              <tr key={loc.strike_location ?? 'unattributed'}>
                <td>
                  {loc.strike_location ?? (
                    <span className="muted">not stated</span>
                  )}
                </td>
                <td className="num">{loc.shot_count}</td>
                <td className="num">{fmt(loc.smash_factor.median, meta, 'smash_factor')}</td>
                <td className="num">{fmt(loc.carry.median, meta, 'carry')}</td>
                <td className="num">{fmt(loc.ball_speed.median, meta, 'ball_speed')}</td>
                <td className="num">{fmt(loc.apex.median, meta, 'apex')}</td>
                <td className="num">{fmtNum(loc.dispersion.carry_iqr, 1)}</td>
                <td className="num">{fmtNum(loc.dispersion.spin_axis_iqr, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
