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

/**
 * The only two conversions between "the number a person types" and "the
 * fraction the API stores".
 *
 * Every percentage in this form crosses here exactly once, in `onSubmit`, and
 * comes back exactly once, in `handleEdit`. The alternative - keeping the form
 * in fractions and converting per input - puts the conversion inside
 * react-hook-form's `setValueAs`, which `form.reset()` bypasses: the box would
 * show a percent after an edit and a fraction after a strategy change, from
 * the same field, with no way to tell which is which.
 *
 * These five fields were previously fractions under a "(%)" label, so typing
 * "15" was refused by the input's own `max="1"` and the box showed "0.15"
 * where it said 15%. That is the same shape of bug as the idle cash of issue
 * #2: the number entered is not the number used.
 */
const frac = (percent: number | null | undefined): number => (percent ?? 0) / 100
const pct = (fraction: number | null | undefined): number => (fraction ?? 0) * 100

const profileSchema = z.object({
  name: z.string().min(1, 'Profile name is required'),
  strategy_type: z.enum(['sma_crossover', 'rsi_reversion', 'momentum_breakout']),
  // Percentages are 1-100 here, matching the "(%)" on the labels and the
  // numbers a person types. 0.15 x 10 mirrors app/schemas.py
  // StrategyProfileBase: those used to be 0.10 x 5 in this file while the API
  // defaulted to 0.15 x 10, and a form that pre-fills the lower pair creates a
  // profile structurally capped at 50% of the account - the idle cash of issue
  // #2, arrived at by typing nothing.
  risk_max_position_pct: z.number().min(1).max(100).default(15),
  risk_max_daily_loss_pct: z.number().min(1).max(100).default(5),
  risk_max_concurrent_positions: z.number().int().min(1).max(20).default(10),
  symbols: z.array(z.string().min(1)).min(1, 'At least one symbol is required'),
  fast_period: z.number().int().min(1).max(200).optional(),
  slow_period: z.number().int().min(1).max(200).optional(),
  period: z.number().int().min(2).max(50).optional(),
  oversold: z.number().int().min(1).max(99).optional(),
  overbought: z.number().int().min(1).max(99).optional(),
  lookback: z.number().int().min(2).max(100).optional(),
  position_size_pct: z.number().min(1).max(100).optional(),
  allow_fractional_shares: z.boolean().default(false),
  cash_sweep_enabled: z.boolean().default(false),
  cash_sweep_symbol: z.string().optional(),
  cash_sweep_pct: z.number().min(0).max(100).optional()
})
  // The contradiction the backend also rejects (issue #2). Duplicated here
  // because the API's 422 arrives as a JSON `detail` array that
  // `err.response?.data?.detail` renders as "[object Object]" - so relying on
  // the server alone would show the user an unreadable error for a mistake the
  // form could have caught while they were typing.
  //
  // Percent against percent, so the comparison is unaffected by the scale
  // change above - only the units both sides are now expressed in.
  .refine(
    (d) => d.position_size_pct == null || d.position_size_pct <= d.risk_max_position_pct,
    {
      message:
        'Position Size % cannot exceed Max Position Size %. The strategy orders at Position Size % and the risk check refuses anything above Max Position Size %, so every order would be rejected.',
      path: ['position_size_pct'],
    }
  )

type ProfileForm = z.infer<typeof profileSchema>

const emptyForm = {
  name: '',
  strategy_type: 'sma_crossover' as StrategyType,
  risk_max_position_pct: 15,
  risk_max_daily_loss_pct: 5,
  risk_max_concurrent_positions: 10,
  symbols: ['AAPL'],
  fast_period: 10,
  slow_period: 30,
  position_size_pct: 15,
  allow_fractional_shares: false,
  cash_sweep_enabled: false,
  cash_sweep_symbol: 'SPY',
  cash_sweep_pct: 20
}

/**
 * The most of the account a set of caps can deploy, live, as the form stands.
 *
 * The same arithmetic as `StrategyProfile.max_deployable_pct` on the server
 * (which is what the saved-profile cards render), recomputed here so the
 * configurator can show the ceiling *while the numbers are being changed* -
 * which is the only moment it is useful. 5 positions at 15% is 75%, and a
 * user lowering nothing at all has a profile that can never deploy a quarter
 * of their account.
 *
 * Takes and returns percents, to match the form the numbers are read out of.
 * A fraction in here would put this display on a different scale from the box
 * beside it - `min(cap, sizePct)` comparing 15 against 0.15 always picks
 * 0.15, which reads as "position size is the binding cap" no matter what the
 * user typed.
 */
function deployableCeiling(positions: number, positionCap: number, sizePct?: number): number {
  const per = Math.min(positionCap, sizePct ?? positionCap)
  return Math.min(100, Math.max(0, positions) * Math.max(0, per))
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
    // `strategyDefaults` is stored in the API's units - a fraction - because it
    // is also what `describeParams` reads back off a saved profile. The form
    // is in percent, so it crosses here.
    const d = strategyDefaults[type]
    if (type === 'sma_crossover') {
      form.setValue('fast_period', d.fast_period)
      form.setValue('slow_period', d.slow_period)
      form.setValue('position_size_pct', pct(d.position_size_pct))
    } else if (type === 'rsi_reversion') {
      form.setValue('period', d.period)
      form.setValue('oversold', d.oversold)
      form.setValue('overbought', d.overbought)
      form.setValue('position_size_pct', pct(d.position_size_pct))
    } else {
      form.setValue('lookback', d.lookback)
      form.setValue('position_size_pct', pct(d.position_size_pct))
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
      // `parameters` is the API's shape, so it is fractions from here down.
      // The initialiser is unreachable for any of the three enum values below -
      // each branch reassigns - and stays a fraction because it comes straight
      // out of `strategyDefaults` rather than out of the form.
      let parameters: StrategyParameters = { position_size_pct: d.position_size_pct }
      if (activeStrategy === 'sma_crossover') {
        parameters = { fast_period: data.fast_period, slow_period: data.slow_period, position_size_pct: frac(data.position_size_pct) }
      } else if (activeStrategy === 'rsi_reversion') {
        parameters = { period: data.period, oversold: data.oversold, overbought: data.overbought, position_size_pct: frac(data.position_size_pct) }
      } else {
        parameters = { lookback: data.lookback, position_size_pct: frac(data.position_size_pct) }
      }

      // The sweep lives inside the parameters JSON rather than in a column,
      // because it is a strategy choice rather than a risk limit - and because
      // an operator who never wants it should not have a column in the schema
      // for it. The `enabled` flag is what decides, so an unfinished block
      // (symbol typed, pct left at 0) is inert on the worker side too.
      if (data.cash_sweep_enabled && data.cash_sweep_symbol) {
        parameters.cash_sweep = {
          enabled: true,
          symbol: data.cash_sweep_symbol.toUpperCase(),
          pct: frac(data.cash_sweep_pct)
        }
      }

      const profileData: ProfileCreate = {
        name: data.name,
        strategy_type: data.strategy_type,
        parameters,
        risk_max_position_pct: frac(data.risk_max_position_pct),
        risk_max_daily_loss_pct: frac(data.risk_max_daily_loss_pct),
        risk_max_concurrent_positions: data.risk_max_concurrent_positions,
        symbols: data.symbols,
        allow_fractional_shares: data.allow_fractional_shares
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
      risk_max_position_pct: pct(profile.risk_max_position_pct),
      risk_max_daily_loss_pct: pct(profile.risk_max_daily_loss_pct),
      risk_max_concurrent_positions: profile.risk_max_concurrent_positions,
      symbols: profile.symbols,
      fast_period: profile.parameters?.fast_period || 10,
      slow_period: profile.parameters?.slow_period || 30,
      period: profile.parameters?.period || 14,
      oversold: profile.parameters?.oversold || 30,
      overbought: profile.parameters?.overbought || 70,
      lookback: profile.parameters?.lookback || 20,
      position_size_pct: pct(profile.parameters?.position_size_pct ?? 0.15),
      allow_fractional_shares: profile.allow_fractional_shares ?? false,
      // `?? emptyForm...` rather than a bare literal, so a profile with no
      // sweep block opens the configurator on the same values a new one does.
      cash_sweep_enabled: profile.parameters?.cash_sweep?.enabled ?? false,
      cash_sweep_symbol: profile.parameters?.cash_sweep?.symbol ?? 'SPY',
      cash_sweep_pct: pct(profile.parameters?.cash_sweep?.pct ?? 0.2)
    })
  }

  const resetForm = () => {
    setEditingId(null)
    form.reset(emptyForm)
    setActiveStrategy('sma_crossover')
  }

  // min/max are in the *form's* units, so the percentage field is 1-100 and not
  // the 0.01-1 it is stored in. These attributes are what stops a browser
  // accepting a number the schema will then reject, and a field whose bounds
  // disagree with its label is worse than one with no bounds at all - see the
  // note above `frac()`.
  const strategyFields = () => {
    switch (activeStrategy) {
      case 'sma_crossover':
        return [
          { name: 'fast_period', label: 'Fast Period', min: 1, max: 200, step: 1 },
          { name: 'slow_period', label: 'Slow Period', min: 1, max: 200, step: 1 },
          { name: 'position_size_pct', label: 'Position Size %', min: 1, max: 100, step: 1 }
        ]
      case 'rsi_reversion':
        return [
          { name: 'period', label: 'RSI Period', min: 2, max: 50, step: 1 },
          { name: 'oversold', label: 'Oversold Threshold', min: 1, max: 99, step: 1 },
          { name: 'overbought', label: 'Overbought Threshold', min: 1, max: 99, step: 1 },
          { name: 'position_size_pct', label: 'Position Size %', min: 1, max: 100, step: 1 }
        ]
      default:
        return [
          { name: 'lookback', label: 'Lookback Period', min: 2, max: 100, step: 1 },
          { name: 'position_size_pct', label: 'Position Size %', min: 1, max: 100, step: 1 }
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
    <div className="max-w-4xl mx-auto space-y-4 sm:space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-body">Strategy</h1>
          <p className="mt-1 text-sm text-muted">
            The profile the bot is trading right now, and how to switch it.
          </p>
        </div>
        <button
          onClick={() => { resetForm(); setShowForm(v => !v) }}
          className="btn-secondary shrink-0"
        >
          {showForm ? <ChevronDown className="h-4 w-4 mr-1 flex-shrink-0" /> : <Plus className="h-4 w-4 mr-1 flex-shrink-0" />}
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
              {/* The ceiling those three imply (issue #2). `max_deployable_pct`
                  comes from the server so the number here and the number the
                  worker logs are the same arithmetic, not two copies of it.
                  Coloured as a warning below 95% because that is the shape of
                  the original complaint - a profile that looks configured and
                  can never reach a quarter of the account. */}
              <div>
                <div className="text-xs text-muted">Can deploy at most</div>
                <div className={`text-lg font-semibold ${
                  activeProfile.max_deployable_pct < 0.95
                    ? 'text-warning-fg'
                    : 'text-success-fg'
                }`}>
                  {(activeProfile.max_deployable_pct * 100).toFixed(0)}%
                </div>
              </div>
            </div>

            {activeProfile.max_deployable_pct < 0.95 && (
              <p className="mt-2 text-xs text-muted">
                {activeProfile.risk_max_concurrent_positions} positions at{' '}
                {(
                  Math.min(
                    activeProfile.risk_max_position_pct,
                    activeProfile.parameters?.position_size_pct ?? activeProfile.risk_max_position_pct
                  ) * 100
                ).toFixed(0)}
                % leaves the rest of the account in cash permanently - waiting
                for more signals will not change it. Raise Max concurrent, Max
                position, or Position size %.
              </p>
            )}

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
          <div className="overflow-x-auto scrollbar-thin">
            {/* min-w so the columns keep their size and this scrolls, instead
                of the browser compressing Activate / Edit / Delete into one
                character per line. See the dashboard tables for the same
                reasoning. */}
            <table className="w-full min-w-[720px]">
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
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
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
                    <label className="label" htmlFor={field.name}>{field.label}</label>
                    <input
                      id={field.name}
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
                  <label className="label" htmlFor="risk_max_position_pct">
                    Max Position Size (%)
                  </label>
                  <input
                    id="risk_max_position_pct"
                    {...form.register('risk_max_position_pct', { valueAsNumber: true })}
                    type="number" className="input" min="1" max="100" step="1"
                  />
                </div>
                <div>
                  <label className="label" htmlFor="risk_max_daily_loss_pct">
                    Max Daily Loss (%)
                  </label>
                  <input
                    id="risk_max_daily_loss_pct"
                    {...form.register('risk_max_daily_loss_pct', { valueAsNumber: true })}
                    type="number" className="input" min="1" max="100" step="1"
                  />
                </div>
                <div>
                  <label className="label" htmlFor="risk_max_concurrent_positions">
                    Max Concurrent Positions
                  </label>
                  <input
                    id="risk_max_concurrent_positions"
                    {...form.register('risk_max_concurrent_positions', { valueAsNumber: true })}
                    type="number" className="input" min="1" max="20" step="1"
                  />
                </div>
              </div>

              {/* The deployment ceiling, live (issue #2).

                  This is the number that answers "why is my cash sitting
                  idle", and it was nowhere to be seen. Five positions at 15%
                  is 75%: a quarter of the account is unreachable by
                  construction, no amount of waiting for a signal changes it,
                  and the profile just looks configured.

                  Both caps are shown because either can be the binding one -
                  the strategy sizes with Position Size %, the risk check
                  refuses above Max Position Size %, and the smaller of the two
                  is what actually gets ordered. `form.watch` rather than state
                  so this re-renders as the numbers are typed. */}
              {(() => {
                const cap = form.watch('risk_max_position_pct') ?? 0
                const count = form.watch('risk_max_concurrent_positions') ?? 0
                const sizePct = form.watch('position_size_pct')
                const ceiling = deployableCeiling(count, cap, sizePct)
                const binding = Math.min(cap, sizePct ?? cap)
                const cappedAt100 = count * binding > 100
                return (
                  <div className="mt-4 bg-canvas rounded-lg p-4">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="text-sm font-medium text-body">
                        Can deploy at most
                      </span>
                      <span className={`text-2xl font-bold ${
                        ceiling < 95 ? 'text-warning-fg' : 'text-success-fg'
                      }`}>
                        {ceiling.toFixed(0)}%
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-muted">
                      {count} position{count === 1 ? '' : 's'} x{' '}
                      {binding.toFixed(0)}%
                      {binding === cap ? ' (max position size)' : ' (position size)'}
                      {cappedAt100 && ' - capped at 100% of the account'}
                      {' '}of equity. The rest stays in cash no matter how many
                      signals arrive.
                    </p>
                  </div>
                )
              })()}

              {/* Fractional sizing (issue #2). Opt-in because Alpaca accepts a
                  fractional quantity on a market order only - a fractional
                  limit order is refused by the broker. Off by default, so a
                  profile that never considered it keeps trading whole shares. */}
              <label className="mt-4 flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  {...form.register('allow_fractional_shares')}
                  className="mt-1 h-4 w-4 rounded border-line"
                />
                <span>
                  <span className="text-sm font-medium text-body">
                    Allow fractional shares
                  </span>
                  <span className="block text-xs text-muted">
                    Deploys the remainder of a position instead of discarding up
                    to one share per signal. Market orders only, which is all
                    this bot sends.
                  </span>
                </span>
              </label>
            </div>

            {/* Cash sweep (issue #2). The other half of the idle-cash problem:
                a strategy trades on a signal, so a quiet week leaves the cash
                uninvested indefinitely. Off by default - this opens a real
                position and the operator should have to ask for it. */}
            <div className="border-t border-line pt-6">
              <h3 className="font-medium text-body mb-1">Cash sweep</h3>
              <p className="text-xs text-muted mb-4">
                When the strategy finds no signal, buy a broad instrument with a
                slice of whatever is still undeployed. Bounded per cycle, and
                never larger than the Max Position Size above.
              </p>
              <label className="flex items-start gap-2 cursor-pointer mb-4">
                <input
                  type="checkbox"
                  {...form.register('cash_sweep_enabled')}
                  className="mt-1 h-4 w-4 rounded border-line"
                />
                <span className="text-sm font-medium text-body">
                  Sweep idle cash
                </span>
              </label>
              {form.watch('cash_sweep_enabled') && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="label">Instrument</label>
                    <input
                      {...form.register('cash_sweep_symbol')}
                      className="input"
                      placeholder="SPY"
                    />
                  </div>
                  <div>
                    <label className="label" htmlFor="cash_sweep_pct">
                      Max % of equity per cycle
                    </label>
                    <input
                      id="cash_sweep_pct"
                      {...form.register('cash_sweep_pct', { valueAsNumber: true })}
                      type="number"
                      className="input"
                      min="0"
                      max="100"
                      step="1"
                    />
                    <p className="mt-1 text-xs text-muted">
                      A per-cycle ceiling, not a target. Leave it at 0 to keep
                      the sweep off.
                    </p>
                  </div>
                </div>
              )}
            </div>

            <div className="border-t border-line pt-6">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
                <h3 className="font-medium text-body">Trading Symbols</h3>
                <button type="button" onClick={() => appendSymbol('')} className="btn-secondary text-sm flex-shrink-0">
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

            <div className="flex flex-wrap items-center justify-end gap-3 border-t border-line pt-4">
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
