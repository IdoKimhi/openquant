import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Bot,
  Check,
  Copy,
  History,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { agentKeysApi, AgentKey, AgentRateLimits, Scope, ScopeInfo } from '../api/agentKeys'
import { auditApi, ActorKind, AuditEntry } from '../api/audit'
import { safeFormat, safeFormatDistance } from '../lib/format'

/**
 * The base URL an agent should call.
 *
 * Derived from the browser rather than configured, because the answer depends
 * on how the operator reached the app - localhost during development, a LAN IP
 * or a hostname in production. A hardcoded value is wrong in three of those.
 * `/api` is nginx's prefix and is stripped before the request reaches FastAPI
 * (AGENTS.md gotcha 1), so it is part of the public URL, not the app's.
 */
function useApiBaseUrl(): string {
  return useMemo(() => {
    if (typeof window === 'undefined') return '/api'
    return `${window.location.origin}/api`
  }, [])
}

function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard API needs a secure context. The text is selectable on screen
      // either way, so a failure here is not worth an error dialog.
    }
  }

  return (
    <button type="button" onClick={copy} className="btn-secondary py-1.5 px-3 text-xs">
      {copied ? <Check className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />}
      {copied ? 'Copied' : label}
    </button>
  )
}

/**
 * Who changed what, newest first.
 *
 * This is the answer to the question a scoped key makes necessary: once an
 * agent can move the bot, "was that me or the human" stops being obvious, and
 * `last_used_at` on the key cannot answer it - it records that a key was
 * presented, not what it did, and it stops recording the moment the key is
 * revoked.
 *
 * Reads are absent by design, so what appears here is exclusively actions.
 */
function ActivityLog({ revision }: { revision: number }) {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [actor, setActor] = useState<ActorKind | 'all'>('all')
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setFailed(false)
      const { data } = await auditApi.list({
        limit: 25,
        // `all` is the absence of a filter, not a value the API knows.
        ...(actor === 'all' ? {} : { actor }),
      })
      setEntries(data)
    } catch {
      // A trail that failed to load is a problem in itself, but it is not
      // worth an error state over the keys list - say so inline and move on.
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [actor])

  useEffect(() => {
    load()
    // `revision` is bumped by the parent after a key is created or revoked,
    // both of which are audited actions this table should immediately show.
  }, [load, revision])

  const filters: Array<{ value: ActorKind | 'all'; label: string }> = [
    { value: 'all', label: 'Everyone' },
    { value: 'agent', label: 'Agents' },
    { value: 'user', label: 'Admin' },
    { value: 'anonymous', label: 'Unauthenticated' },
  ]

  return (
    <section className="card space-y-4">
      <div className="flex items-center gap-3">
        <History className="h-5 w-5 text-accent" aria-hidden="true" />
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-body">Recent actions</h2>
          <p className="text-sm text-muted">
            Every state-changing call, whoever made it. Refused attempts are
            included, because the route never ran and logged nothing itself.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="btn-secondary py-1.5 px-3 text-xs shrink-0"
          aria-label="Refresh activity"
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          )}
        </button>
      </div>

      <div className="flex gap-1.5 flex-wrap" role="group" aria-label="Filter by actor">
        {filters.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setActor(f.value)}
            aria-pressed={actor === f.value}
            className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${
              actor === f.value
                ? 'border-accent bg-accent-soft text-body'
                : 'border-line text-muted hover:border-line-strong'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {failed ? (
        <p className="text-sm text-muted">Could not load the activity log.</p>
      ) : loading ? (
        <p className="flex items-center text-sm text-muted">
          <Loader2 className="h-4 w-4 mr-2 animate-spin" aria-hidden="true" />
          Loading...
        </p>
      ) : entries.length === 0 ? (
        <p className="text-sm text-muted">Nothing has changed anything yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {entries.map((entry) => (
            <li key={entry.id} className="py-2.5 flex items-start gap-3">
              <span
                className={`badge shrink-0 mt-0.5 ${
                  entry.status_code >= 400
                    ? 'badge-danger'
                    : entry.actor_kind === 'agent'
                      ? 'badge-info'
                      : 'badge-gray'
                }`}
              >
                {entry.actor_kind === 'anonymous' ? 'unauth' : entry.actor_kind}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-body">
                  <span className="font-mono text-xs text-muted">{entry.method}</span>{' '}
                  <span className="font-mono text-xs">{entry.path}</span>
                </p>
                {entry.detail && (
                  <p className="text-xs text-subtle font-mono mt-0.5 break-all">
                    {entry.detail}
                  </p>
                )}
                <p className="text-xs text-muted mt-0.5">
                  {entry.actor_label ?? 'no key'} &middot; {safeFormatDistance(entry.created_at)}
                  {entry.status_code >= 400 && (
                    <span className="text-danger-fg"> &middot; refused ({entry.status_code})</span>
                  )}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function AgentsPage() {
  const baseUrl = useApiBaseUrl()
  const [keys, setKeys] = useState<AgentKey[]>([])
  const [scopes, setScopes] = useState<ScopeInfo[]>([])
  const [rateLimits, setRateLimits] = useState<AgentRateLimits | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Bumped whenever an audited action this page took has just happened, so
  // the activity table below reloads without the user hitting refresh.
  const [auditRevision, setAuditRevision] = useState(0)

  // New-key form state
  const [label, setLabel] = useState('')
  const [selected, setSelected] = useState<Scope[]>([])
  const [creating, setCreating] = useState(false)

  // One-time reveal
  const [revealed, setRevealed] = useState<{ key: string; label: string } | null>(null)
  const [revoking, setRevoking] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setError(null)
      const [keyList, scopeList] = await Promise.all([agentKeysApi.list(), agentKeysApi.scopes()])
      setKeys(keyList.data)
      setScopes(scopeList.data.scopes)
      setRateLimits(scopeList.data.rate_limits)
      // Default the form from what the backend will actually grant, rather than
      // hardcoding ['read'] here and letting the two drift.
      setSelected(scopeList.data.scopes.filter((s) => s.granted_by_default).map((s) => s.name))
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to load agent keys')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const toggleScope = (name: Scope) => {
    setSelected((current) =>
      current.includes(name) ? current.filter((s) => s !== name) : [...current, name],
    )
  }

  const createKey = async () => {
    if (!label.trim() || selected.length === 0) return
    setCreating(true)
    setError(null)
    try {
      const response = await agentKeysApi.create({ label: label.trim(), scopes: selected })
      setRevealed({ key: response.data.key, label: response.data.label })
      setLabel('')
      setAuditRevision((n) => n + 1)
      await load()
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to create key')
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (id: number) => {
    setRevoking(id)
    setError(null)
    try {
      await agentKeysApi.revoke(id)
      setAuditRevision((n) => n + 1)
      await load()
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to revoke key')
    } finally {
      setRevoking(null)
    }
  }

  const active = keys.filter((k) => !k.revoked_at)
  const revoked = keys.filter((k) => k.revoked_at)
  const hasKillScope = selected.includes('bot:kill')

  const curlExample = `curl -H "Authorization: Bearer $OPENQUANT_KEY" \\
  ${baseUrl}/dashboard/account`

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-body">Agents</h1>
        <p className="mt-1 text-sm text-muted">
          Connect an external agent to this instance. It authenticates with its own key, not your
          session, and only gets the permissions you grant it.
        </p>
      </div>

      {error && (
        <div className="bg-danger-soft border border-line-danger rounded-lg p-4 text-danger-fg text-sm" role="alert">
          {error}
        </div>
      )}

      {/* --- Endpoint ------------------------------------------------- */}
      <section className="card space-y-4">
        <div className="flex items-start gap-3">
          <ShieldCheck className="h-5 w-5 text-accent mt-0.5 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-body">Endpoint</h2>
            <p className="text-sm text-muted">
              The same API this dashboard uses. There is no separate agent-specific surface.
            </p>
          </div>
        </div>

        <div>
          <label className="label" htmlFor="api-base-url">Base URL</label>
          <div className="flex gap-2">
            <input
              id="api-base-url"
              readOnly
              value={baseUrl}
              onFocus={(e) => e.currentTarget.select()}
              className="input font-mono text-xs"
            />
            <CopyButton value={baseUrl} />
          </div>
          <p className="mt-2 text-xs text-muted">
            Paper trading only. This instance can never place a live order.
          </p>
        </div>

        <div>
          <span className="label">Example</span>
          <pre className="bg-canvas border border-line rounded-md p-3 text-xs font-mono text-muted overflow-x-auto">
{curlExample}
          </pre>
        </div>

        {rateLimits && (
          <div>
            <span className="label">Rate limits</span>
            <p className="text-sm text-muted">
              Every agent key is throttled independently, and a throttled call
              returns <code className="font-mono text-xs">429</code> with a{' '}
              <code className="font-mono text-xs">Retry-After</code> header.
            </p>
            <ul className="mt-2 text-sm text-body">
              <li>
                <span className="font-mono text-xs text-muted">GET</span> &mdash;{' '}
                {rateLimits.read_limit} requests per {rateLimits.window_seconds}s
              </li>
              <li>
                <span className="font-mono text-xs text-muted">POST/PATCH/DELETE</span> &mdash;{' '}
                {rateLimits.write_limit} requests per {rateLimits.window_seconds}s
              </li>
            </ul>
            <p className="mt-2 text-xs text-muted">
              Your own session is not limited. Limits come from{' '}
              <code className="font-mono">AGENT_READ_RATE_LIMIT</code> and{' '}
              <code className="font-mono">AGENT_WRITE_RATE_LIMIT</code> on the server.
            </p>
          </div>
        )}
      </section>

      {/* --- One-time key reveal --------------------------------------- */}
      {revealed && (
        <section className="card space-y-3 border-warning" role="alert">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-warning mt-0.5 shrink-0" aria-hidden="true" />
            <div>
              <h2 className="text-lg font-semibold text-body">Copy this key now</h2>
              <p className="text-sm text-muted">
                It is shown once and never again &mdash; only a hash of it is stored, so it
                cannot be recovered. If you lose it, revoke the key and create another.
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            <input readOnly value={revealed.key} onFocus={(e) => e.currentTarget.select()} className="input font-mono text-xs" />
            <CopyButton value={revealed.key} label="Copy key" />
          </div>
          <button type="button" onClick={() => setRevealed(null)} className="btn-secondary py-1.5 px-3 text-xs">
            <X className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
            I've saved it
          </button>
        </section>
      )}

      {/* --- Create ---------------------------------------------------- */}
      <section className="card space-y-4">
        <div className="flex items-start gap-3">
          <KeyRound className="h-5 w-5 text-accent mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <h2 className="text-lg font-semibold text-body">Create a key</h2>
            <p className="text-sm text-muted">Name it after what the agent is for, so revoking later is obvious.</p>
          </div>
        </div>

        <div>
          <label className="label" htmlFor="key-label">Label</label>
          <input
            id="key-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. daily-review-agent"
            className="input"
            maxLength={100}
          />
        </div>

        <fieldset>
          <legend className="label">Permissions</legend>
          <div className="space-y-2">
            {scopes.map((scope) => (
              <label
                key={scope.name}
                className="flex items-start gap-3 border border-line rounded-md p-3 cursor-pointer hover:bg-surface-hover transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(scope.name)}
                  onChange={() => toggleScope(scope.name)}
                  className="mt-1 h-4 w-4 rounded border-line text-accent focus:ring-accent-ring"
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <span className="text-sm font-medium text-body">{scope.label}</span>
                    <code className="text-xs font-mono text-subtle">{scope.name}</code>
                    {scope.granted_by_default && (
                      <span className="badge-info">{scope.name === 'read' ? 'default' : 'recommended'}</span>
                    )}
                  </span>
                  <span className="block text-sm text-muted mt-0.5">{scope.description}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {hasKillScope && (
          <div className="bg-danger-soft border border-line-danger rounded-md p-3 text-sm text-danger-fg flex gap-2">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" aria-hidden="true" />
            <span>
              This key can cancel every open order and close every position with a single
              request. Only grant it to something you would trust with the account.
            </span>
          </div>
        )}

        <button
          type="button"
          onClick={createKey}
          disabled={!label.trim() || selected.length === 0 || creating}
          className="btn-primary"
        >
          {creating ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" aria-hidden="true" />
          ) : (
            <Plus className="h-4 w-4 mr-2" aria-hidden="true" />
          )}
          Create key
        </button>
      </section>

      {/* --- Existing keys --------------------------------------------- */}
      <section className="card space-y-4">
        <div className="flex items-center gap-3">
          <Bot className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2 className="text-lg font-semibold text-body">Active keys</h2>
        </div>

        {loading ? (
          <div className="flex items-center text-sm text-muted">
            <Loader2 className="h-4 w-4 mr-2 animate-spin" aria-hidden="true" />
            Loading...
          </div>
        ) : active.length === 0 ? (
          <p className="text-sm text-muted">No active keys. Nothing outside this dashboard can reach the API.</p>
        ) : (
          <ul className="space-y-3">
            {active.map((key) => (
              <li key={key.id} className="border border-line rounded-md p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-body">{key.label}</span>
                      <code className="text-xs font-mono text-subtle">{key.key_prefix}&hellip;</code>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {key.scopes.map((scope) => (
                        <span key={scope} className="badge-gray font-mono text-xs">{scope}</span>
                      ))}
                    </div>
                    <p className="mt-2 text-xs text-muted">
                      Created {safeFormatDistance(key.created_at)} &middot;{' '}
                      {key.last_used_at ? `Last used ${safeFormatDistance(key.last_used_at)}` : 'Never used'}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => revoke(key.id)}
                    disabled={revoking === key.id}
                    className="btn-danger py-1.5 px-3 text-xs shrink-0"
                  >
                    {revoking === key.id ? (
                      <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" aria-hidden="true" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
                    )}
                    Revoke
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {revoked.length > 0 && (
        <section className="card space-y-3">
          <h2 className="text-sm font-semibold text-muted uppercase tracking-wide">Revoked</h2>
          <ul className="space-y-2">
            {revoked.map((key) => (
              <li key={key.id} className="flex items-center justify-between text-sm">
                <span className="text-subtle line-through">{key.label}</span>
                <span className="text-xs text-subtle">{safeFormat(key.revoked_at, 'MMM d, yyyy')}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <ActivityLog revision={auditRevision} />
    </div>
  )
}
