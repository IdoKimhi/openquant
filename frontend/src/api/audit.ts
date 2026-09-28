// Audit log API
//
// Hand-written to match `AuditLogResponse` in app/schemas.py, per AGENTS.md
// gotcha 3: a missing field renders as `undefined` rather than throwing, so
// drift is silent.
import { api } from './client'

/**
 * "user" is the admin session, "agent" is an `oq_` key, "anonymous" is an
 * attempt that never authenticated - a missing header, or a token that failed.
 * Anonymous is not a third kind of caller, it is the absence of one, and it
 * is worth showing precisely because a refused attempt is the interesting row.
 */
export type ActorKind = 'user' | 'agent' | 'anonymous'

/** Mirrors AuditLogResponse. */
export interface AuditEntry {
  id: number
  created_at: string
  actor_kind: ActorKind
  actor_label: string | null
  key_id: number | null
  method: string
  path: string
  /** Route template, e.g. "/profiles/{profile_id}/activate". */
  action: string
  status_code: number
  /** JSON string written by the route, or null. Never a request body. */
  detail: string | null
  client_ip: string | null
}

export const auditApi = {
  list: (params?: { limit?: number; actor?: ActorKind }) =>
    api.get<AuditEntry[]>('/dashboard/audit-log', { params }),
}
