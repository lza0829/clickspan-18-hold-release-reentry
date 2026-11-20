async function req(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json()
}

export const api = {
  health: () => req('/api/health'),
  overview: () => req('/api/overview'),
  visitors: () => req('/api/visitors'),
  sessions: (visitorId) =>
    req(visitorId ? `/api/sessions?visitor_id=${encodeURIComponent(visitorId)}` : '/api/sessions'),
  session: (id) => req(`/api/sessions/${encodeURIComponent(id)}`),
  quarantine: (status) =>
    req(status ? `/api/quarantine?status=${encodeURIComponent(status)}` : '/api/quarantine'),
  holdHit: (hit, reason) => req('/api/quarantine/hold', { method: 'POST', body: JSON.stringify({ hit, reason }) }),
  releaseHolds: (holdIds) =>
    req('/api/quarantine/release', { method: 'POST', body: JSON.stringify({ hold_ids: holdIds }) }),
  reentries: () => req('/api/reentries'),
  releaseBatches: () => req('/api/release-batches'),
  ingest: () => req('/api/ingest/run', { method: 'POST' }),
  proofs: (sessionId) =>
    req(sessionId ? `/api/session-proofs?session_id=${encodeURIComponent(sessionId)}` : '/api/session-proofs'),
  settings: () => req('/api/settings'),
}
