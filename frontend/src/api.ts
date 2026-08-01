import type {
  BrowseResult,
  Dispersion,
  Facets,
  InsightAnswer,
  Metadata,
  PerClub,
  Session,
  SessionDetail,
  Shot,
  StrikeVsOutcome,
  Trend,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body; the status text is all we have */
    }
    throw new Error(`${response.status}: ${detail}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function query(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const api = {
  metadata: () => request<Metadata>('/api/metadata'),

  sessions: () => request<Session[]>('/api/sessions'),
  session: (id: number) => request<SessionDetail>(`/api/sessions/${id}`),
  createSession: (body: Partial<Session>) =>
    request<Session>('/api/sessions', { method: 'POST', body: JSON.stringify(body) }),
  updateSession: (id: number, body: Partial<Session>) =>
    request<Session>(`/api/sessions/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteSession: (id: number) => request<void>(`/api/sessions/${id}`, { method: 'DELETE' }),
  confirmSession: (id: number) =>
    request<Session>(`/api/sessions/${id}/confirm`, { method: 'POST' }),
  unconfirmSession: (id: number) =>
    request<Session>(`/api/sessions/${id}/unconfirm`, { method: 'POST' }),

  createShot: (sessionId: number, body: Partial<Shot>) =>
    request<Shot>(`/api/sessions/${sessionId}/shots`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateShot: (id: number, body: Partial<Shot>) =>
    request<Shot>(`/api/shots/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteShot: (id: number) => request<void>(`/api/shots/${id}`, { method: 'DELETE' }),
  bulkClub: (sessionId: number, club: string, fromIndex: number, toIndex: number) =>
    request<{ updated: number; shots: Shot[] }>(`/api/sessions/${sessionId}/bulk-club`, {
      method: 'POST',
      body: JSON.stringify({ club, from_index: fromIndex, to_index: toIndex }),
    }),

  perClub: () => request<PerClub>('/api/analysis/per-club'),
  strikeVsOutcome: (club?: string) =>
    request<StrikeVsOutcome>(`/api/analysis/strike-vs-outcome${query({ club })}`),
  trend: (metric: string) => request<Trend>(`/api/analysis/trend${query({ metric })}`),
  dispersion: (club?: string) =>
    request<Dispersion>(`/api/analysis/dispersion${query({ club })}`),
  facets: () => request<Facets>('/api/analysis/facets'),

  browse: (params: Record<string, string | number | boolean | null | undefined>) =>
    request<BrowseResult>(`/api/shots${query(params)}`),

  ask: (question: string) =>
    request<InsightAnswer>('/api/insight/ask', {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),

  artifactUrl: (artifactId: number) => `/api/artifacts/${artifactId}/file`,
}
