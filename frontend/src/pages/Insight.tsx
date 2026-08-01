import { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../api'
import type { InsightAnswer } from '../types'
import { Card, Empty, ErrorBanner } from '../components/common'
import { AXIS, GRID_STROKE, TipBox } from '../components/charts'

const EXAMPLES = [
  'Which strike location costs me the most carry with the 7 iron?',
  'How has my median 7 iron carry moved session over session?',
  'What share of my shots are struck on the heel, by club?',
  'Show me every shot where I said it was thin.',
]

/**
 * Spec section 8, phase 5 -- the only genuinely agentic component.
 *
 *   "A read-only text-to-SQL agent over the confirmed dataset. Natural language
 *    questions, generated query, results rendered as table or chart, with the
 *    SQL always visible. Read-only credentials."
 *
 * The SQL panel is not collapsible and is rendered whether the query succeeded,
 * failed, or was rejected by the guard. An answer you cannot check is not an
 * answer.
 */
export function Insight() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<InsightAnswer | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function ask(text: string) {
    if (!text.trim()) return
    setBusy(true)
    setAnswer(null)
    try {
      setAnswer(await api.ask(text))
      setError(null)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>Ask</h1>
      </div>
      <p className="page-note">
        Ask a question in plain English. It is turned into a SQLite query, run against
        the confirmed dataset on a read-only connection, and shown to you along with
        the query itself — so you can check the answer rather than take it on trust.
        Only reviewed shots are visible to it.
      </p>

      <ErrorBanner error={error} />

      <Card>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault()
            void ask(question)
          }}
        >
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Which strike location costs me the most carry?"
            style={{ flex: 1, minWidth: 260 }}
          />
          <button className="primary" type="submit" disabled={busy || !question.trim()}>
            {busy ? 'Thinking…' : 'Ask'}
          </button>
        </form>
        <div className="row" style={{ marginTop: 10 }}>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              style={{ fontSize: 12 }}
              onClick={() => {
                setQuestion(example)
                void ask(example)
              }}
            >
              {example}
            </button>
          ))}
        </div>
      </Card>

      {answer && <AnswerPanel answer={answer} />}
    </>
  )
}

function AnswerPanel({ answer }: { answer: InsightAnswer }) {
  return (
    <>
      {answer.explanation && (
        <Card title="What this measures">
          <p style={{ margin: 0 }}>{answer.explanation}</p>
        </Card>
      )}

      <Card title="Query" note="Always shown, whether it ran or not.">
        {answer.sql ? (
          <pre className="sql">{answer.sql}</pre>
        ) : (
          <Empty>No query was generated.</Empty>
        )}
        {answer.error && (
          <div className="error" style={{ marginTop: 10, marginBottom: 0 }}>
            {answer.error}
          </div>
        )}
      </Card>

      {answer.rows.length > 0 && (
        <>
          <ResultChart answer={answer} />
          <Card
            title="Results"
            note={
              answer.truncated
                ? `First ${answer.row_count} rows — the result was truncated.`
                : `${answer.row_count} row${answer.row_count === 1 ? '' : 's'}`
            }
          >
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    {answer.columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {answer.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j} className={typeof cell === 'number' ? 'num' : ''}>
                          {cell === null ? (
                            <span className="missing">null</span>
                          ) : typeof cell === 'number' ? (
                            Number.isInteger(cell) ? cell : cell.toFixed(2)
                          ) : (
                            String(cell)
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  )
}

/**
 * Render the result as a chart when the shape allows it: a text-ish first
 * column and one numeric column. Anything else stays a table, which is the
 * honest default for arbitrary query output.
 */
function ResultChart({ answer }: { answer: InsightAnswer }) {
  const hint = answer.chart_hint
  if (!hint || hint === 'table' || answer.rows.length < 2) return null

  const numericIndex = answer.columns.findIndex(
    (_, i) => typeof answer.rows[0][i] === 'number',
  )
  if (numericIndex < 0) return null

  const labelIndex = answer.columns.findIndex((_, i) => typeof answer.rows[0][i] === 'string')
  if (labelIndex < 0) return null

  const data = answer.rows.map((row) => ({
    label: String(row[labelIndex]),
    value: Number(row[numericIndex]),
  }))
  const valueName = answer.columns[numericIndex]

  return (
    <Card title={valueName} note={`by ${answer.columns[labelIndex]}`}>
      <div style={{ width: '100%', height: 260 }}>
        <ResponsiveContainer>
          {hint === 'line' ? (
            <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
              <CartesianGrid stroke={GRID_STROKE} vertical={false} />
              <XAxis dataKey="label" {...AXIS} axisLine={{ stroke: GRID_STROKE }} />
              <YAxis {...AXIS} axisLine={false} width={52} />
              <Tooltip
                cursor={{ stroke: 'var(--border-strong)' }}
                content={({ active, payload, label }) =>
                  active && payload?.length ? (
                    <TipBox
                      title={String(label)}
                      rows={[[valueName, String(payload[0].value)]]}
                    />
                  ) : null
                }
              />
              <Line
                type="linear"
                dataKey="value"
                stroke="var(--series-1)"
                strokeWidth={2}
                dot={{ r: 4, fill: 'var(--series-1)', strokeWidth: 2, stroke: 'var(--surface-1)' }}
                isAnimationActive={false}
              />
            </LineChart>
          ) : (
            <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
              <CartesianGrid stroke={GRID_STROKE} vertical={false} />
              <XAxis dataKey="label" {...AXIS} axisLine={{ stroke: GRID_STROKE }} />
              <YAxis {...AXIS} axisLine={false} width={52} />
              <Tooltip
                cursor={{ fill: 'var(--surface-2)' }}
                content={({ active, payload, label }) =>
                  active && payload?.length ? (
                    <TipBox
                      title={String(label)}
                      rows={[[valueName, String(payload[0].value)]]}
                    />
                  ) : null
                }
              />
              <Bar
                dataKey="value"
                fill="var(--seq-450)"
                radius={[4, 4, 0, 0]}
                isAnimationActive={false}
              />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </Card>
  )
}
