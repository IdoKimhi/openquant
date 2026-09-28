// Schedule Page - Configure bot schedule and settings
import { useState, useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Save, Loader2, AlertCircle, Check } from 'lucide-react'
import { botConfigApi, BotConfig, BotConfigUpdate } from '../api/botConfig'
import { profilesApi, Profile } from '../api/profiles'

const scheduleSchema = z.object({
  schedule_cron: z.string().min(1, 'Cron expression is required'),
  market_hours_only: z.boolean().default(true),
  active_profile_id: z.number().nullable().optional()
})

type ScheduleForm = z.infer<typeof scheduleSchema>

/**
 * Turn an IANA zone into something readable in a sentence.
 *
 * "America/New_York" -> "New York". Deliberately not a hardcoded ET/UTC
 * lookup: the zone is the backend's BOT_TIMEZONE, so anything this page
 * prints about it has to come from the value rather than from an assumption
 * about what it usually is.
 */
function zoneLabel(tz: string): string {
  const city = tz.split('/').pop() ?? tz
  return city.replace(/_/g, ' ')
}

// The presets below are US equity session times. If the deployment runs on a
// different zone they are still valid cron - they just no longer mean "during
// market hours", which is worth saying out loud rather than leaving the
// operator to work out that "9-16" is now somebody else's trading day.
const US_MARKET_TZ = 'America/New_York'

export function SchedulePage() {
  const [config, setConfig] = useState<BotConfig | null>(null)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  
  const form = useForm<ScheduleForm>({
    resolver: zodResolver(scheduleSchema),
    defaultValues: {
      schedule_cron: '*/5 9-16 * * MON-FRI',
      market_hours_only: true,
      active_profile_id: null
    }
  })
  
  const loadData = async () => {
    try {
      const [configRes, profilesRes] = await Promise.all([
        botConfigApi.get(),
        profilesApi.list()
      ])
      setConfig(configRes.data)
      setProfiles(profilesRes.data)
      
      form.reset({
        schedule_cron: configRes.data.schedule_cron,
        market_hours_only: configRes.data.market_hours_only,
        active_profile_id: configRes.data.active_profile_id
      })
    } catch (err) {
      setError('Failed to load configuration')
    } finally {
    }
  }
  
  useEffect(() => {
    loadData()
  }, [])
  
  const onSubmit = async (data: ScheduleForm) => {
    setSaving(true)
    setError(null)
    setSuccess(null)
    
    try {
      const updateData: BotConfigUpdate = {
        schedule_cron: data.schedule_cron,
        market_hours_only: data.market_hours_only,
        active_profile_id: data.active_profile_id || null
      }
      
      await botConfigApi.update(updateData)
      setSuccess('Schedule updated successfully!')
      loadData()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to update schedule')
    } finally {
      setSaving(false)
    }
  }
  
  const parseCron = (cron: string): string => {
    // Simple cron description
    const parts = cron.split(' ')
    if (parts.length !== 5) return cron
    
    const [minute, hour, _dayOfMonth, _month, dayOfWeek] = parts
    
    let desc = ''
    
    // Minute
    if (minute.startsWith('*/')) {
      desc += `Every ${minute.slice(2)} minutes`
    } else if (minute === '0') {
      desc += 'At minute 0'
    } else {
      desc += `At minute ${minute}`
    }
    
    // Hour
    if (hour.includes('-')) {
      desc += ` between ${hour.replace('-', ' and ')}:00`
    } else if (hour !== '*') {
      desc += ` at ${hour}:00`
    }
    
    // Day of week
    const days = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
    if (dayOfWeek !== '*') {
      const dayNames = dayOfWeek.split(',').map(d => {
        const num = parseInt(d)
        return days[num] || d
      }).join(', ')
      desc += ` on ${dayNames}`
    }
    
    return desc || cron
  }
  
  const currentCronDesc = config ? parseCron(config.schedule_cron) : ''

  // Rendered from the API, not asserted here. This page previously printed
  // "Timezone: UTC" while the worker ran on America/New_York, so a schedule
  // written against the label fired four hours off - the exact bug the
  // timezone tests exist for, in the one place a user reads the label.
  const tz = config?.cron_timezone ?? US_MARKET_TZ
  const tzName = zoneLabel(tz)
  const onMarketTz = tz === US_MARKET_TZ

  // Built from tzName rather than hardcoded "ET", so the preset labels cannot
  // drift away from the zone the worker is actually using.
  const cronPresets = [
    { value: '*/5 9-16 * * MON-FRI', label: `Every 5 min during market hours (9:30-16:00 ${tzName}, Mon-Fri)` },
    { value: '*/15 9-16 * * MON-FRI', label: 'Every 15 min during market hours' },
    { value: '*/30 9-16 * * MON-FRI', label: 'Every 30 min during market hours' },
    { value: '0 9-16 * * MON-FRI', label: 'Hourly during market hours' },
    { value: '0 9 * * MON-FRI', label: `Daily at market open (9:30 ${tzName})` },
    { value: '0 16 * * MON-FRI', label: `Daily at market close (16:00 ${tzName})` }
  ]
  
  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-body">Schedule & Settings</h1>
        <p className="mt-1 text-sm text-muted">
          Configure when the bot runs and which strategy profile to use.
        </p>
      </div>
      
      {/* Current Status */}
      <div className="card">
        <h2 className="text-lg font-medium text-body mb-4">Current Configuration</h2>
        {config && (
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-muted">Schedule</dt>
              <dd className="font-mono text-body">{config.schedule_cron}</dd>
              <dd className="text-muted mt-1">
                {currentCronDesc} <span className="text-subtle">({tzName})</span>
              </dd>
            </div>
            <div>
              <dt className="text-muted">Cron Timezone</dt>
              <dd className="font-mono text-body">{tz}</dd>
              <dd className="text-muted mt-1">
                Set by <code className="font-mono">BOT_TIMEZONE</code> on the server
              </dd>
            </div>
            <div>
              <dt className="text-muted">Market Hours Only</dt>
              <dd className="font-medium text-body">
                {config.market_hours_only ? 'Yes' : 'No'}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Active Profile</dt>
              <dd className="font-medium text-body">
                {config.active_profile_id
                  ? profiles.find(p => p.id === config.active_profile_id)?.name || `ID: ${config.active_profile_id}`
                  : 'None selected'}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Bot Status</dt>
              <dd className="font-medium">
                <span className={`badge ${config.is_running ? 'badge-success' : 'badge-gray'}`}>
                  {config.is_running ? 'Running' : 'Stopped'}
                </span>
              </dd>
            </div>
          </dl>
        )}
      </div>
      
      {/* Schedule Form */}
      <div className="card">
        <h2 className="text-lg font-medium text-body mb-4">Update Schedule</h2>
        
        {(error || success) && (
          <div className={`mb-4 p-3 rounded-md text-sm flex items-center ${
            error ? 'bg-danger-soft border border-line-danger text-danger-fg' :
            'bg-success-soft border border-line-success text-success-fg'
          }`}>
            {error && <AlertCircle className="h-4 w-4 mr-2" />}
            {success && <Check className="h-4 w-4 mr-2" />}
            {error || success}
          </div>
        )}
        
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          {/* Cron Presets */}
          <div>
            <label className="label">Quick Presets</label>
            <div className="grid grid-cols-1 gap-2">
              {cronPresets.map(preset => {
                const selected = form.watch('schedule_cron') === preset.value
                return (
                  <button
                    key={preset.value}
                    type="button"
                    onClick={() => form.setValue('schedule_cron', preset.value, { shouldDirty: true, shouldValidate: true })}
                    aria-pressed={selected}
                    className={`relative cursor-pointer p-3 border rounded-lg transition-colors text-left ${
                      selected
                        ? 'border-accent bg-accent-soft'
                        : 'border-line hover:border-line-strong'
                    }`}
                  >
                    <div className="flex items-center">
                      <div className={`h-4 w-4 border rounded ${
                        selected
                          ? 'border-accent bg-accent'
                          : 'border-line-strong'
                      } relative`}>
                        {selected && (
                          <Check className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 h-3 w-3 text-white" />
                        )}
                      </div>
                      <span className="ml-3 text-sm text-body">{preset.label}</span>
                    </div>
                  </button>
                )
              })}
            </div>
            
            {/* Custom Cron */}
            <div className="mt-3">
              <label className="label">Custom Cron Expression</label>
              <input
                {...form.register('schedule_cron')}
                className="input font-mono"
                placeholder="e.g., */10 9-16 * * MON-FRI"
              />
              {form.formState.errors.schedule_cron && (
                <p className="mt-1 text-sm text-danger-fg">{form.formState.errors.schedule_cron.message}</p>
              )}
              <p className="mt-1 text-xs text-muted">
                Uses standard cron format: minute hour day-of-month month day-of-week
                <br />
                Times below are <span className="font-medium text-body">{tzName}</span>{' '}
                <span className="font-mono">({tz})</span>, not UTC
              </p>
              {!onMarketTz && (
                <p className="mt-2 p-2 rounded-md text-xs bg-danger-soft border border-line-danger text-danger-fg">
                  This server's <code className="font-mono">BOT_TIMEZONE</code> is{' '}
                  <code className="font-mono">{tz}</code>, not the US market timezone.
                  The presets below still fire at 9:00-16:00 local, which is no longer
                  the US trading session.
                </p>
              )}
            </div>
          </div>
          
          {/* Market Hours Toggle */}
          <div className="border-t border-line pt-6">
            <div className="flex items-center justify-between">
              <div>
                <label className="label">Market Hours Only</label>
                <p className="text-sm text-muted">
                  Restrict the trading cycle to the regular US session
                  (9:30 AM - 4:00 PM ET, Mon-Fri).
                </p>
                {/* This toggle is stored and displayed but the worker never
                    reads it - the cycle gates on Alpaca's market clock
                    unconditionally. Unchecking it currently does nothing, so
                    the control says so rather than implying extended-hours
                    trading is available. */}
                <p className="mt-1 text-xs text-subtle">
                  Not enforced yet: the cycle always checks the broker's market
                  clock, so unchecking this does not enable extended hours.
                </p>
              </div>
              <button
                type="button"
                onClick={() => form.setValue('market_hours_only', !form.watch('market_hours_only'))}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  form.watch('market_hours_only') ? 'bg-accent' : 'bg-line'
                }`}
                role="switch"
                aria-checked={form.watch('market_hours_only')}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-surface transition-transform ${
                    form.watch('market_hours_only') ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          </div>
          
          {/* Active Profile */}
          <div className="border-t border-line pt-6">
            <label className="label">Active Strategy Profile</label>
            <select
              {...form.register('active_profile_id', { valueAsNumber: true })}
              className="input"
            >
              <option value="">-- Select a profile --</option>
              {profiles.filter(p => p.enabled).map(profile => (
                <option key={profile.id} value={profile.id}>
                  {profile.name} ({profile.strategy_type.replace('_', ' ')})
                </option>
              ))}
            </select>
            {profiles.filter(p => p.enabled).length === 0 && (
              <p className="mt-1 text-sm text-muted">
                No enabled profiles available. <a href="/strategy" className="text-accent hover:underline">Create one first</a>.
              </p>
            )}
          </div>
          
          {/* Submit */}
          <button
            type="submit"
            disabled={saving}
            className="btn-primary w-full"
          >
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="h-4 w-4 mr-2" />
                Save Schedule
              </>
            )}
          </button>
        </form>
      </div>
      
      {/* Cron Help */}
      <div className="card bg-canvas border-line">
        <h3 className="font-medium text-body mb-3">Cron Expression Reference</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="text-muted border-b border-line">
                <th className="pb-2 font-medium w-24">Field</th>
                <th className="pb-2 font-medium">Values</th>
                <th className="pb-2 font-medium">Special</th>
              </tr>
            </thead>
            <tbody className="text-body divide-y divide-line">
              <tr><td className="py-2 font-mono">Minute</td><td className="py-2">0-59</td><td className="py-2">*, */n, n-m</td></tr>
              <tr><td className="py-2 font-mono">Hour</td><td className="py-2">0-23 ({tzName})</td><td className="py-2">*, */n, n-m</td></tr>
              <tr><td className="py-2 font-mono">Day of Month</td><td className="py-2">1-31</td><td className="py-2">*, ?, L, W</td></tr>
              <tr><td className="py-2 font-mono">Month</td><td className="py-2">1-12 or JAN-DEC</td><td className="py-2">*, */n</td></tr>
              <tr><td className="py-2 font-mono">Day of Week</td><td className="py-2">0-7 (0 or 7 = Sun) or MON-SUN</td><td className="py-2">*, ?, L, #</td></tr>
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-muted">
          Example: <code className="font-mono bg-line px-1 rounded">*/5 9-16 * * MON-FRI</code>{' '}
          = Every 5 minutes, 9am-4pm {tzName}, Monday-Friday
        </p>
      </div>
    </div>
  )
}