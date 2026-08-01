import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useMeta } from '../meta'
import { describeFlag } from '../format'

export function Card({
  title,
  note,
  actions,
  children,
}: {
  title?: ReactNode
  note?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {note && <div className="muted" style={{ fontSize: 12 }}>{note}</div>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return <div className="error">{message}</div>
}

/**
 * Sample size, rendered next to every aggregate.
 *
 * Spec section 7: "Display sample size next to every aggregate." A median of
 * four shots and a median of forty are different claims and must not look
 * alike.
 */
export function SampleSize({ n }: { n: number }) {
  return <span className="sample">n={n}</span>
}

/** Names the specific validation failure, per section 6. */
export function FlagBadge({ flag }: { flag: string }) {
  const meta = useMeta()
  return (
    <span className="badge flag" title={describeFlag(flag, meta)}>
      {describeFlag(flag, meta)}
    </span>
  )
}

export function FlagList({ flags }: { flags: string[] }) {
  const meta = useMeta()
  if (!flags.length) return null
  return (
    <div className="flag-list">
      {flags.map((flag) => (
        <div key={flag} className="flag-reason">
          <span className="badge flag">needs review</span>
          <span>{describeFlag(flag, meta)}</span>
        </div>
      ))}
    </div>
  )
}

/**
 * An inline-editable cell.
 *
 * Section 6 requires every field to be inline-editable. Edits commit on blur or
 * Enter and revert on Escape, so a mistyped value never silently persists.
 */
export function EditableCell({
  value,
  onCommit,
  type = 'number',
  align = 'right',
  placeholder,
}: {
  value: string | number | null
  onCommit: (next: string | null) => void
  type?: 'number' | 'text'
  align?: 'right' | 'left'
  placeholder?: string
}) {
  const initial = value === null || value === undefined ? '' : String(value)
  const [draft, setDraft] = useState(initial)
  const committed = useRef(initial)

  useEffect(() => {
    // Adopt values that changed underneath us (e.g. a bulk action), but never
    // clobber an edit in progress.
    if (document.activeElement !== inputRef.current) {
      setDraft(initial)
      committed.current = initial
    }
  }, [initial])

  const inputRef = useRef<HTMLInputElement>(null)

  function commit() {
    if (draft === committed.current) return
    committed.current = draft
    onCommit(draft === '' ? null : draft)
  }

  return (
    <input
      ref={inputRef}
      className={`cell-input ${type === 'text' ? 'text' : ''} ${
        draft !== committed.current ? 'dirty' : ''
      }`}
      style={{ textAlign: align }}
      value={draft}
      placeholder={placeholder}
      inputMode={type === 'number' ? 'decimal' : undefined}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          commit()
          ;(e.target as HTMLInputElement).blur()
        } else if (e.key === 'Escape') {
          setDraft(committed.current)
          ;(e.target as HTMLInputElement).blur()
        }
      }}
    />
  )
}

export function Select({
  value,
  options,
  onChange,
  placeholder = '—',
  allowEmpty = true,
}: {
  value: string | null
  options: readonly (string | null)[]
  onChange: (next: string | null) => void
  placeholder?: string
  allowEmpty?: boolean
}) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
    >
      {allowEmpty && <option value="">{placeholder}</option>}
      {options
        .filter((o): o is string => o !== null)
        .map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
    </select>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
    </div>
  )
}
