import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Bot,
  Check,
  Copy,
  KeyRound,
  Loader2,
  Plus,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { agentKeysApi, AgentKey, Scope, ScopeInfo } from '../api/agentKeys'
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

export function AgentsPage() {
  const baseUrl = useApiBaseUrl()
  const [keys, setKeys] = useState<AgentKey[]>([])
  const [scopes, setScopes] = useState<ScopeInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

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
    </div>
  )
}
