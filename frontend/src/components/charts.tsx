import type { ReactNode } from 'react'

/** Shared tooltip shell so every chart's hover layer looks the same. */
export function TipBox({
  title,
  rows,
}: {
  title: ReactNode
  rows: [string, ReactNode][]
}) {
  return (
    <div className="tooltip">
      <div className="tip-title">{title}</div>
      <dl>
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: 'contents' }}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/**
 * Legend for a categorical chart.
 *
 * Present whenever there are two or more series, so identity is never carried
 * by colour alone. A single-series chart gets none — its title names it.
 */
export function Legend({
  items,
}: {
  items: { label: string; color: string }[]
}) {
  if (items.length < 2) return null
  return (
    <ul className="legend">
      {items.map((item) => (
        <li key={item.label}>
          <span className="swatch" style={{ background: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  )
}

export const AXIS = {
  stroke: 'var(--text-muted)',
  fontSize: 11,
  tickLine: false,
} as const

export const GRID_STROKE = 'var(--grid)'
