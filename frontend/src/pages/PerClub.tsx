import { useEffect, useState } from 'react'
import { api } from '../api'
import { useMeta } from '../meta'
import { STAT_KEYS, fmt, metricOf } from '../format'
import type { PerClub as PerClubData } from '../types'
import { Card, Empty, ErrorBanner, SampleSize } from '../components/common'

/**
 * Spec section 7, per-club view.
 *
 *   "shot count, and median plus interquartile range for every metric. Median
 *    not mean. Small samples with fat mishit tails make means misleading."
 *
 * Twelve metrics across every club is more classes than any chart can carry, so
 * this is a table by design — and it doubles as the table view that the light-mode
 * categorical charts elsewhere are required to provide.
 */
export function PerClub() {
  const meta = useMeta()
  const [data, setData] = useState<PerClubData | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    api.perClub().then(setData).catch(setError)
  }, [])

  return (
    <>
      <div className="page-head">
        <h1>Per club</h1>
      </div>
      <p className="page-note">
        Median and interquartile range for every metric, over confirmed shots only.
        Median rather than mean: a single shanked shot should not move the number that
        describes a typical one. The IQR under each median is the spread — a small
        median with a wide IQR is a club you are not controlling.
      </p>

      <ErrorBanner error={error} />

      {!data ? (
        <Empty>Loading…</Empty>
      ) : data.clubs.length === 0 ? (
        <Card title="No confirmed shots">
          <Empty>
            Confirm a session in the review screen and its shots will appear here.
          </Empty>
        </Card>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Club</th>
                <th className="num">Shots</th>
                <th className="num">Sessions</th>
                {STAT_KEYS.map((key) => (
                  <th key={key} className="num">
                    {metricOf(meta, key)?.label ?? key}
                    <br />
                    <span className="unit">{data.units[key]}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.clubs.map((club) => (
                <tr key={club.club ?? 'unassigned'}>
                  <td>
                    <strong>{club.club ?? <span className="muted">no club</span>}</strong>
                  </td>
                  <td className="num">{club.shot_count}</td>
                  <td className="num">{club.session_count}</td>
                  {STAT_KEYS.map((key) => {
                    const summary = club.metrics[key]
                    return (
                      <td key={key} className="num">
                        {summary?.median === null || summary === undefined ? (
                          <span className="missing">—</span>
                        ) : (
                          <>
                            {fmt(summary.median, meta, key)}
                            <br />
                            <span className="iqr">
                              IQR {fmt(summary.iqr, meta, key)}
                            </span>
                            {summary.n !== club.shot_count && (
                              <>
                                <br />
                                <SampleSize n={summary.n} />
                              </>
                            )}
                          </>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="page-note" style={{ marginTop: 14 }}>
        Where a metric shows its own <span className="sample">n=</span>, fewer shots
        carried that value than the club's total — spin reads zero on this unit often
        enough that a spin median can rest on a much smaller sample than the carry
        median beside it.
      </p>
    </>
  )
}
