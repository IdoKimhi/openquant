// Strategy Page - shows what the bot is trading right now, with profile
// switching. The configurator is secondary and stays collapsed by default.
import { useState, useEffect, useCallback } from 'react'
import { useForm, useFieldArray } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { formatDistanceToNow } from 'date-fns'
import {
  Plus, Trash2, Save, Edit, Check, AlertCircle, Loader2, Zap, Brain, TrendingUp,
  ChevronDown, Repeat, Power, Settings2
} from 'lucide-react'
import { profilesApi, ProfileCreate, Profile, StrategyType, StrategyParameters } from '../api/profiles'
import { botConfigApi } from '../api/botConfig'

const strategyOptions: { value: StrategyType; label: string; description: string; icon: React.ReactNode }[] = [
  {
    value: 'sma_crossover',
    label: 'SMA Crossover',
    description: 'Buys the bar the fast SMA crosses above the slow SMA, sells on the cross back down',
    icon: <TrendingUp className="h-5 w-5" />
  },
  {
    value: 'rsi_reversion',
    label: 'RSI Mean Reversion',
    description: 'Buys while RSI is below the oversold level, sells while above overbought',
    icon: <Brain className="h-5 w-5" />
  },
  {
    value: 'momentum_breakout',
    label: 'Momentum Breakout',
    description: 'Buys when price exceeds the prior session highs, sells on a drop below the lows',
    icon: <Zap className="h-5 w-5" />
  }
]

// Every strategy only fires while a reading sits past a threshold, so it is
// silent most of the session. Worth stating on the card: "no signal" is the
// normal case, not a fault.
const strategyNotes: Record<StrategyType, string> = {
  sma_crossover: 'Runs on 1-minute bars. Signals only on the exact bar the two averages cross, so it stays flat between crosses.',
  rsi_reversion: 'Runs on 1-minute bars. Signals only while RSI is past a threshold, not on the crossing bar itself.',
  momentum_breakout: 'Runs on daily bars, compared against live price. Signals while price is outside the prior range.'
}

const strategyDefaults: Record<StrategyType, StrategyParameters> = {
  sma_crossover: { fast_period: 10, slow_period: 30, position_size_pct: 0.10 },
  rsi_reversion: { period: 14, oversold: 30, overbought: 70, position_size_pct: 0.10 },
  momentum_breakout: { lookback: 20, position_size_pct: 0.10 }
}

// Mirror the defaults in app/bot/strategies/*.py so a parameter absent from
// the stored JSON is displayed as the value the strategy will actually use.
function describeParams(profile: Profile): { label: string; value: string }[] {
  const p = { ...strategyDefaults[profile.strategy_type], ...(profile.parameters ?? {}) }
  const out: { label: string; value: string }[] = []
  if (profile.strategy_type === 'sma_crossover') {
    out.push({ label: 'Fast SMA', value: String(p.fast_period) })
    out.push({ label: 'Slow SMA', value: String(p.slow_period) })
  } else if (profile.strategy_type === 'rsi_reversion') {
    out.push({ label: 'RSI period', value: String(p.period) })
    out.push({ label: 'Oversold / Overbought', value: `${p.oversold} / ${p.overbought}` })
  } else {
    out.push({ label: 'Lookback', value: `${p.lookback} sessions` })
  }
  if (p.position_size_pct != null) {
    out.push({ label: 'Position size', value: `${(p.position_size_pct * 100).toFixed(1)}% of equity` })
  }
  return out
}

function strategyLabel(type: StrategyType) {
  return strategyOptions.find(o => o.value === type)?.label ?? type
}

// date-fns throws a RangeError on an invalid Date, which unmounts the page and
// leaves a blank white screen. Any date rendering goes through this.
function safeAgo(value: string | null | undefined) {
  const date = value ? new Date(value) : null
  if (!date || Number.isNaN(date.getTime())) return '-'
  return formatDistanceToNow(date, { addSuffix: true })
}

const profileSchema = z.object({
  name: z.string().min(1, 'Profile name is required'),
  strategy_type: z.enum(['sma_crossover', 'rsi_reversion', 'momentum_breakout']),
  risk_max_position_pct: z.number().min(0.01).max(1).default(0.10),
  risk_max_daily_loss_pct: z.number().min(0.01).max(1).default(0.05),
  risk_max_concurrent_positions: z.number().int().min(1).max(20).default(5),
  symbols: z.array(z.string().min(1)).min(1, 'At least one symbol is required'),
  fast_period: z.number().int().min(1).max(200).optional(),
  slow_period: z.number().int().min(1).max(200).optional(),
  period: z.number().int().min(2).max(50).optional(),
  oversold: z.number().int().min(1).max(99).optional(),
  overbought: z.number().int().min(1).max(99).optional(),
  lookback: z.number().int().min(2).max(100).optional(),
  position_size_pct: z.number().min(0.01).max(1).optional()
})

type ProfileForm = z.infer<typeof profileSchema>

const emptyForm = {
  name: '',
  strategy_type: 'sma_crossover' as StrategyType,
  risk_max_position_pct: 0.10,
  risk_max_daily_loss_pct: 0.05,
  risk_max_concurrent_positions: 5,
  symbols: ['AAPL'],
  fast_period: 10,
  slow_period: 30,
  position_size_pct: 0.10
}

export function StrategyPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  // The profile the worker actually trades. This is bot_config.active_profile_id,
  // NOT StrategyProfile.enabled - they are separate fields and the worker
  // requires both to match (see worker/scheduler.py).
  const [activeProfileId, setActiveProfileId] = useState<number | null>(null)
  const [isRunning, setIsRunning] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [switchingId, setSwitchingId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [activeStrategy, setActiveStrategy] = useState<StrategyType>('sma_crossover')
  const [showSwitcher, setShowSwitcher] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const form = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema),
    defaultValues: emptyForm
  })

  const { fields: symbolFields, append: appendSymbol, remove: removeSymbol } = useFieldArray({
    control: form.control as any,
    name: 'symbols'
  })

  const load = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([profilesApi.list(), botConfigApi.get()])
      setProfiles(p.data)
      setActiveProfileId(c.data.active_profile_id ?? null)
      setIsRunning(c.data.is_running)
    } catch {
      setError('Failed to load profiles')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const activeProfile = profiles.find(p => p.id === activeProfileId) ?? null
  // A profile can be enabled without being the active one, and vice versa.
  // Flagging the mismatch is the whole point of showing the real field.
  const enabledButNotActive = profiles.filter(p => p.enabled && p.id !== activeProfileId)
  const danglingActive = activeProfileId !== null && !activeProfile

  const handleStrategyChange = (type: StrategyType) => {
    setActiveStrategy(type)
    form.setValue('strategy_type', type as any)
    const d = strategyDefaults[type]
    if (type === 'sma_crossover') {
      form.setValue('fast_period', d.fast_period)
      form.setValue('slow_period', d.slow_period)
      form.setValue('position_size_pct', d.position_size_pct)
    } else if (type === 'rsi_reversion') {
      form.setValue('period', d.period)
      form.setValue('oversold', d.oversold)
      form.setValue('overbought', d.overbought)
      form.setValue('position_size_pct', d.position_size_pct)
    } else {
      form.setValue('lookback', d.lookback)
      form.setValue('position_size_pct', d.position_size_pct)
    }
  }

  const handleActivate = async (id: number) => {
    if (isRunning && !window.confirm(
      'The bot is running. Switching now takes effect on the next cycle. Continue?'
    )) return
    setSwitchingId(id)
    setError(null)
    try {
      await profilesApi.activate(id)
      await load()
      setShowSwitcher(false)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to activate profile')
    } finally {
      setSwitchingId(null)
    }
  }

  const onSubmit = async (data: ProfileForm) => {
    setSaving(true)
    setError(null)
    try {
      const d = strategyDefaults[activeStrategy]
      let parameters: StrategyParameters = { position_size_pct: d.position_size_pct }
      if (activeStrategy === 'sma_crossover') {
        parameters = { fast_period: data.fast_period, slow_period: data.slow_period, position_size_pct: data.position_size_pct }
      } else if (activeStrategy === 'rsi_reversion') {
        parameters = { period: data.period, oversold: data.oversold, overbought: data.overbought, position_size_pct: data.position_size_pct }
      } else {
        parameters = { lookback: data.lookback, position_size_pct: data.position_size_pct }
      }

      const profileData: ProfileCreate = {
        name: data.name,
        strategy_type: data.strategy_type,
        parameters,
        risk_max_position_pct: data.risk_max_position_pct,
        risk_max_daily_loss_pct: data.risk_max_daily_loss_pct,
        risk_max_concurrent_positions: data.risk_max_concurrent_positions,
        symbols: data.symbols
      }

      if (editingId) {
        await profilesApi.update(editingId, profileData)
      } else {
        await profilesApi.create(profileData)
      }

      await load()
      resetForm()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save profile')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!window.confirm('Are you sure you want to delete this profile?')) return
    try {
      await profilesApi.delete(id)
      await load()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to delete profile')
    }
  }

  const handleEdit = (profile: Profile) => {
    setEditingId(profile.id)
    setActiveStrategy(profile.strategy_type)
    setShowForm(true)
    form.reset({
      name: profile.name,
      strategy_type: profile.strategy_type,
      risk_max_position_pct: profile.risk_max_position_pct,
      risk_max_daily_loss_pct: profile.risk_max_daily_loss_pct,
      risk_max_concurrent_positions: profile.risk_max_concurrent_positions,
      symbols: profile.symbols,
      fast_period: profile.parameters?.fast_period || 10,
      slow_period: profile.parameters?.slow_period || 30,
      period: profile.parameters?.period || 14,
      oversold: profile.parameters?.oversold || 30,
      overbought: profile.parameters?.overbought || 70,
      lookback: profile.parameters?.lookback || 20,
      position_size_pct: profile.parameters?.position_size_pct || 0.10
    })
  }

  const resetForm = () => {
    setEditingId(null)
    form.reset(emptyForm)
    setActiveStrategy('sma_crossover')
  }

  const strategyFields = () => {
    switch (activeStrategy) {
      case 'sma_crossover':
        return [
          { name: 'fast_period', label: 'Fast Period', min: 1, max: 200, step: 1 },
          { name: 'slow_period', label: 'Slow Period', min: 1, max: 200, step: 1 },
          { name: 'position_size_pct', label: 'Position Size %', min: 0.01, max: 1, step: 0.01 }
        ]
      case 'rsi_reversion':
        return [
          { name: 'period', label: 'RSI Period', min: 2, max: 50, step: 1 },
          { name: 'oversold', label: 'Oversold Threshold', min: 1, max: 99, step: 1 },
          { name: 'overbought', label: 'Overbought Threshold', min: 1, max: 99, step: 1 },
          { name: 'position_size_pct', label: 'Position Size %', min: 0.01, max: 1, step: 0.01 }
        ]
      default:
        return [
          { name: 'lookback', label: 'Lookback Period', min: 2, max: 100, step: 1 },
          { name: 'position_size_pct', label: 'Position Size %', min: 0.01, max: 1, step: 0.01 }
        ]
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-accent" />
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-body">Strategy</h1>
          <p className="mt-1 text-sm text-muted">
            The profile the bot is trading right now, and how to switch it.
          </p>
        </div>
        <button
          onClick={() => { resetForm(); setShowForm(v => !v) }}
          className="btn-secondary shrink-0"
        >
          {showForm ? <ChevronDown className="h-4 w-4 mr-1" /> : <Plus className="h-4 w-4 mr-1" />}
          {showForm ? 'Hide configurator' : 'New profile'}
        </button>
      </div>

      {error && (
        <div className="p-3 bg-danger-soft border border-line-danger rounded-md text-sm text-danger-fg flex items-center">
          <AlertCircle className="h-4 w-4 mr-2 shrink-0" />
          {error}
        </div>
      )}

      {/* Current strategy - the primary object of this page */}
      <div className="card border-2 border-accent ring-2 ring-accent-ring">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="badge-info">
                <Power className="h-3 w-3 mr-1" />
                Currently trading
              </span>
              <span className={isRunning ? 'badge-success' : 'badge-gray'}>
                Bot {isRunning ? 'running' : 'stopped'}
              </span>
            </div>
            {activeProfile ? (
              <>
                <h2 className="text-2xl font-bold text-body truncate">
                  {activeProfile.name}
                </h2>
                <p className="text-sm text-muted mt-0.5">
                  {strategyLabel(activeProfile.strategy_type)}
                </p>
              </>
            ) : (
              <h2 className="text-2xl font-bold text-body">No active profile</h2>
            )}
          </div>
          {profiles.length > 0 && (
            <button
              onClick={() => setShowSwitcher(v => !v)}
              className="btn-primary shrink-0"
            >
              <Repeat className="h-4 w-4 mr-1" />
              Change
            </button>
          )}
        </div>

        {!activeProfile && (
          <div className="p-3 bg-warning-soft border border-line-warning rounded-md text-sm text-warning-fg">
            {danglingActive
              ? `The bot is pointed at profile #${activeProfileId}, which no longer exists. Every cycle will skip until you pick a profile.`
              : 'The bot has no profile to trade. Pick one below, or create a profile.'}
          </div>
        )}

        {activeProfile && (
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-muted uppercase tracking-wide mb-2">
                Symbols ({activeProfile.symbols.length})
              </div>
              <div className="flex flex-wrap gap-2">
                {activeProfile.symbols.map(s => (
                  <span key={s} className="badge bg-surface-hover text-body font-mono">
                    {s}
                  </span>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 pt-4 border-t border-line">
              {describeParams(activeProfile).map(p => (
                <div key={p.label}>
                  <div className="text-xs text-muted">{p.label}</div>
                  <div className="text-lg font-semibold text-body">{p.value}</div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 pt-4 border-t border-line">
              <div>
                <div className="text-xs text-muted">Max position</div>
                <div className="text-lg font-semibold text-body">
                  {(activeProfile.risk_max_position_pct * 100).toFixed(0)}%
                </div>
              </div>
              <div>
                <div className="text-xs text-muted">Max daily loss</div>
                <div className="text-lg font-semibold text-body">
                  {(activeProfile.risk_max_daily_loss_pct * 100).toFixed(0)}%
                </div>
              </div>
              <div>
                <div className="text-xs text-muted">Max concurrent</div>
                <div className="text-lg font-semibold text-body">
                  {activeProfile.risk_max_concurrent_positions}
                </div>
              </div>
            </div>

            <p className="text-xs text-muted pt-3 border-t border-line">
              {strategyNotes[activeProfile.strategy_type]}
            </p>
            <p className="text-xs text-subtle">
              Last edited {safeAgo(activeProfile.updated_at)}
            </p>
          </div>
        )}
      </div>

      {/* Switcher */}
      {showSwitcher && profiles.length > 0 && (
        <div className="card">
          <h2 className="text-lg font-medium text-body mb-1">Switch profile</h2>
          <p className="text-sm text-muted mb-4">
            Takes effect on the next trading cycle. The bot resolves the profile by
            both id and enabled flag, so these are set together.
          </p>
          <div className="space-y-2">
            {profiles.map(profile => {
              const isActive = profile.id === activeProfileId
              return (
                <button
                  key={profile.id}
                  onClick={() => !isActive && handleActivate(profile.id)}
                  disabled={isActive || switchingId !== null}
                  className={`w-full text-left p-4 rounded-lg border-2 transition-all ${
                    isActive
                      ? 'border-accent bg-accent-soft'
                      : 'border-line hover:border-accent hover:bg-canvas'
                  } disabled:cursor-default`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        {isActive && <span className="h-2.5 w-2.5 rounded-full bg-accent shrink-0" />}
                        <span className={`truncate ${isActive ? 'text-xl font-bold text-body' : 'text-base font-medium text-body'}`}>
                          {profile.name}
                        </span>
                      </div>
                      <p className="text-sm text-muted mt-0.5">
                        {strategyLabel(profile.strategy_type)} · {profile.symbols.join(', ')}
                      </p>
                    </div>
                    {isActive ? (
                      <span className="badge-info shrink-0">Trading now</span>
                    ) : switchingId === profile.id ? (
                      <Loader2 className="h-4 w-4 animate-spin text-accent shrink-0" />
                    ) : (
                      <span className="btn-secondary text-sm shrink-0 pointer-events-none">Switch to</span>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {enabledButNotActive.length > 0 && (
        <div className="p-3 bg-warning-soft border border-line-warning rounded-md text-sm text-warning-fg">
          Enabled but not trading: {enabledButNotActive.map(p => p.name).join(', ')}.
          The bot only trades the profile marked above.
        </div>
      )}

      {/* All profiles */}
      <div className="card">
        <h2 className="text-lg font-medium text-body mb-4">All profiles</h2>
        {profiles.length === 0 ? (
          <div className="text-center py-8 text-muted">
            No profiles yet. Use "New profile" to create one.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-sm text-muted border-b border-line">
                  <th className="pb-3 font-medium">Name</th>
                  <th className="pb-3 font-medium">Strategy</th>
                  <th className="pb-3 font-medium">Symbols</th>
                  <th className="pb-3 font-medium">Risk</th>
                  <th className="pb-3 font-medium">Status</th>
                  <th className="pb-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {profiles.map(profile => {
                  const isActive = profile.id === activeProfileId
                  return (
                    <tr key={profile.id} className={isActive ? 'bg-accent-soft/60' : editingId === profile.id ? 'bg-canvas' : ''}>
                      <td className="py-3">
                        <span className={isActive ? 'font-bold text-accent' : 'font-medium text-body'}>
                          {profile.name}
                        </span>
                      </td>
                      <td className="py-3 text-sm text-muted">
                        {strategyLabel(profile.strategy_type)}
                      </td>
                      <td className="py-3 text-sm text-muted font-mono">
                        {profile.symbols.join(', ')}
                      </td>
                      <td className="py-3 text-sm text-muted">
                        Pos {(profile.risk_max_position_pct * 100).toFixed(0)}% · Loss{' '}
                        {(profile.risk_max_daily_loss_pct * 100).toFixed(0)}% · Max{' '}
                        {profile.risk_max_concurrent_positions}
                      </td>
                      <td className="py-3">
                        {isActive ? (
                          <span className="badge-info">Trading</span>
                        ) : profile.enabled ? (
                          <span className="badge-warning">Enabled, idle</span>
                        ) : (
                          <span className="badge-gray">Idle</span>
                        )}
                      </td>
                      <td className="py-3">
                        <div className="flex items-center gap-2">
                          {!isActive && (
                            <button
                              onClick={() => handleActivate(profile.id)}
                              disabled={switchingId !== null}
                              className="btn-success text-sm"
                              title="Make this the active profile"
                            >
                              {switchingId === profile.id
                                ? <Loader2 className="h-4 w-4 animate-spin" />
                                : <Check className="h-4 w-4" />}
                            </button>
                          )}
                          <button
                            onClick={() => handleEdit(profile)}
                            className="btn-secondary text-sm"
                            title="Edit"
                          >
                            <Edit className="h-4 w-4" />
                          </button>
                          <button
                            onClick={() => handleDelete(profile.id)}
                            className="btn-danger text-sm"
                            title="Delete"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Configurator - secondary, collapsed by default */}
      {showForm && (
        <div className="card">
          <h2 className="text-lg font-medium text-body mb-4 flex items-center">
            <Settings2 className="h-5 w-5 mr-2 text-subtle" />
            {editingId ? 'Edit Profile' : 'Create New Profile'}
          </h2>

          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="label">Profile Name</label>
                <input {...form.register('name')} className="input" placeholder="My SMA Strategy" />
                {form.formState.errors.name && (
                  <p className="mt-1 text-sm text-danger-fg">{form.formState.errors.name.message}</p>
                )}
              </div>
              <div>
                <label className="label">Strategy Type</label>
                <div className="grid grid-cols-3 gap-2">
                  {strategyOptions.map(option => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => handleStrategyChange(option.value)}
                      className={`p-3 rounded-lg border-2 text-left transition-all ${
                        activeStrategy === option.value
                          ? 'border-accent bg-accent-soft'
                          : 'border-line hover:border-line-strong'
                      }`}
                    >
                      <span className={activeStrategy === option.value ? 'text-accent' : 'text-subtle'}>
                        {option.icon}
                      </span>
                      <p className="mt-1 text-xs text-muted">{option.label}</p>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="bg-canvas rounded-lg p-4">
              <h3 className="font-medium text-body mb-4">Strategy Parameters</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {strategyFields().map(field => (
                  <div key={field.name}>
                    <label className="label">{field.label}</label>
                    <input
                      {...form.register(field.name as any, { valueAsNumber: true })}
                      type="number"
                      className="input"
                      min={field.min}
                      max={field.max}
                      step={field.step}
                    />
                    {(form.formState.errors as any)[field.name] && (
                      <p className="mt-1 text-sm text-danger-fg">{(form.formState.errors as any)[field.name].message}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <div className="border-t border-line pt-6">
              <h3 className="font-medium text-body mb-4">Risk Management</h3>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label className="label">Max Position Size (%)</label>
                  <input
                    {...form.register('risk_max_position_pct', { valueAsNumber: true })}
                    type="number" className="input" min="0.01" max="1" step="0.01"
                  />
                </div>
                <div>
                  <label className="label">Max Daily Loss (%)</label>
                  <input
                    {...form.register('risk_max_daily_loss_pct', { valueAsNumber: true })}
                    type="number" className="input" min="0.01" max="1" step="0.01"
                  />
                </div>
                <div>
                  <label className="label">Max Concurrent Positions</label>
                  <input
                    {...form.register('risk_max_concurrent_positions', { valueAsNumber: true })}
                    type="number" className="input" min="1" max="20" step="1"
                  />
                </div>
              </div>
            </div>

            <div className="border-t border-line pt-6">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-medium text-body">Trading Symbols</h3>
                <button type="button" onClick={() => appendSymbol('')} className="btn-secondary text-sm">
                  <Plus className="h-4 w-4 mr-1" />
                  Add Symbol
                </button>
              </div>
              <div className="space-y-2">
                {symbolFields.map((field: { id: string }, index: number) => (
                  <div key={field.id} className="flex items-center gap-2">
                    <input
                      {...form.register(`symbols.${index}`)}
                      className="input flex-1"
                      placeholder="e.g., AAPL"
                    />
                    <button
                      type="button"
                      onClick={() => removeSymbol(index)}
                      className="text-subtle hover:text-danger-fg"
                    >
                      <Trash2 className="h-5 w-5" />
                    </button>
                  </div>
                ))}
              </div>
              {form.formState.errors.symbols && (
                <p className="mt-1 text-sm text-danger-fg">{form.formState.errors.symbols.message}</p>
              )}
            </div>

            <div className="flex items-center justify-end gap-3 border-t border-line pt-4">
              <button type="button" onClick={() => { resetForm(); setShowForm(false) }} className="btn-secondary">
                Cancel
              </button>
              <button type="submit" disabled={saving} className="btn-primary">
                {saving ? (
                  <><Loader2 className="h-4 w-4 mr-2 animate-spin" />Saving...</>
                ) : (
                  <><Save className="h-4 w-4 mr-2" />{editingId ? 'Update Profile' : 'Create Profile'}</>
                )}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
