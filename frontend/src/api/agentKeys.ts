// Agent key API
//
// Hand-written to match the Pydantic models in app/schemas.py. Per AGENTS.md
// gotcha 3 these drift silently - a missing field renders as `undefined` rather
// than throwing - so keep them in step.
import { api } from './client'

/** Mirrors app.authz.ALL_SCOPES. */
export type Scope = 'read' | 'config:write' | 'bot:control' | 'bot:kill'

export interface ScopeInfo {
  name: Scope
  label: string
  description: string
  danger: 'low' | 'medium' | 'high'
  granted_by_default: boolean
}

/** Mirrors AgentKeyResponse. There is no `key` field here by design. */
export interface AgentKey {
  id: number
  label: string
  key_prefix: string
  scopes: Scope[]
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

/** Mirrors AgentKeyCreated - the only response that ever contains the key. */
export interface AgentKeyCreated extends AgentKey {
  key: string
}

/** Mirrors AgentRateLimits. The budgets a new key will actually be held to. */
export interface AgentRateLimits {
  requests_per_window: number
  window_seconds: number
  read_limit: number
  write_limit: number
}

export interface AgentKeyCreate {
  label: string
  scopes: Scope[]
}

export const agentKeysApi = {
  list: () => api.get<AgentKey[]>('/agent-keys'),
  scopes: () => api.get<{ scopes: ScopeInfo[]; rate_limits: AgentRateLimits }>('/agent-keys/scopes'),
  create: (data: AgentKeyCreate) => api.post<AgentKeyCreated>('/agent-keys', data),
  revoke: (id: number) => api.delete(`/agent-keys/${id}`),
}
