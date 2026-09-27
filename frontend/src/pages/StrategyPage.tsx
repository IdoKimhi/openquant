// Strategy Page - Create and manage strategy profiles (simplified)
import { useState, useEffect } from 'react'
import { useForm, useFieldArray } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Plus, Trash2, Save, Edit, Check, AlertCircle, Loader2, Zap, Brain, TrendingUp } from 'lucide-react'
import { profilesApi, ProfileCreate, Profile, StrategyType } from '../api/profiles'

const strategyOptions: { value: StrategyType; label: string; description: string; icon: React.ReactNode }[] = [
  {
    value: 'sma_crossover',
    label: 'SMA Crossover',
    description: 'Buy when fast SMA crosses above slow SMA, sell when it crosses below',
    icon: <TrendingUp className="h-5 w-5" />
  },
  {
    value: 'rsi_reversion',
    label: 'RSI Mean Reversion',
    description: 'Buy when RSI is oversold (< 30), sell when overbought (> 70)',
    icon: <Brain className="h-5 w-5" />
  },
  {
    value: 'momentum_breakout',
    label: 'Momentum Breakout',
    description: 'Buy on breakout above recent high, sell on breakdown below recent low',
    icon: <Zap className="h-5 w-5" />
  }
]

const profileSchema = z.object({
  name: z.string().min(1, 'Profile name is required'),
  strategy_type: z.enum(['sma_crossover', 'rsi_reversion', 'momentum_breakout']),
  risk_max_position_pct: z.number().min(0.01).max(1).default(0.10),
  risk_max_daily_loss_pct: z.number().min(0.01).max(1).default(0.05),
  risk_max_concurrent_positions: z.number().int().min(1).max(20).default(5),
  symbols: z.array(z.string().min(1)).min(1, 'At least one symbol is required'),
  // Strategy-specific parameters (flattened)
  fast_period: z.number().int().min(1).max(200).optional(),
  slow_period: z.number().int().min(1).max(200).optional(),
  period: z.number().int().min(2).max(50).optional(),
  oversold: z.number().int().min(1).max(99).optional(),
  overbought: z.number().int().min(1).max(99).optional(),
  lookback: z.number().int().min(2).max(100).optional(),
  position_size_pct: z.number().min(0.01).max(1).optional()
})

type ProfileForm = z.infer<typeof profileSchema>

export function StrategyPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [activeStrategy, setActiveStrategy] = useState<StrategyType>('sma_crossover')
  const [error, setError] = useState<string | null>(null)
  
  const form = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
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
  })
  
  const { fields: symbolFields, append: appendSymbol, remove: removeSymbol } = useFieldArray({
    control: form.control as any,
    name: 'symbols'
  })
  
  const loadProfiles = async () => {
    try {
      const response = await profilesApi.list()
      setProfiles(response.data)
    } catch (err) {
      setError('Failed to load profiles')
    } finally {
      setLoading(false)
    }
  }
  
  useEffect(() => {
    loadProfiles()
  }, [])
  
  const handleStrategyChange = (type: StrategyType) => {
    setActiveStrategy(type)
    form.setValue('strategy_type', type as any)
    
    // Reset strategy-specific defaults
    if (type === 'sma_crossover') {
      form.setValue('fast_period', 10)
      form.setValue('slow_period', 30)
      form.setValue('position_size_pct', 0.10)
    } else if (type === 'rsi_reversion') {
      form.setValue('period', 14)
      form.setValue('oversold', 30)
      form.setValue('overbought', 70)
      form.setValue('position_size_pct', 0.10)
    } else if (type === 'momentum_breakout') {
      form.setValue('lookback', 20)
      form.setValue('position_size_pct', 0.10)
    }
  }
  
  const onSubmit = async (data: ProfileForm) => {
    setSaving(true)
    setError(null)
    
    try {
      let parameters: any = {}
      
      if (activeStrategy === 'sma_crossover') {
        parameters = {
          fast_period: data.fast_period,
          slow_period: data.slow_period,
          position_size_pct: data.position_size_pct
        }
      } else if (activeStrategy === 'rsi_reversion') {
        parameters = {
          period: data.period,
          oversold: data.oversold,
          overbought: data.overbought,
          position_size_pct: data.position_size_pct
        }
      } else if (activeStrategy === 'momentum_breakout') {
        parameters = {
          lookback: data.lookback,
          position_size_pct: data.position_size_pct
        }
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
      
      loadProfiles()
      resetForm()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save profile')
    } finally {
      setSaving(false)
    }
  }
  
  const handleActivate = async (id: number) => {
    try {
      await profilesApi.activate(id)
      loadProfiles()
    } catch (err) {
      setError('Failed to activate profile')
    }
  }
  
  const handleDelete = async (id: number) => {
    if (!window.confirm('Are you sure you want to delete this profile?')) return
    try {
      await profilesApi.delete(id)
      loadProfiles()
    } catch (err) {
      setError('Failed to delete profile')
    }
  }
  
  const handleEdit = (profile: Profile) => {
    setEditingId(profile.id)
    setActiveStrategy(profile.strategy_type)
    
    form.reset({
      name: profile.name,
      strategy_type: profile.strategy_type,
      risk_max_position_pct: profile.risk_max_position_pct,
      risk_max_daily_loss_pct: profile.risk_max_daily_loss_pct,
      risk_max_concurrent_positions: profile.risk_max_concurrent_positions,
      symbols: profile.symbols,
      fast_period: profile.parameters.fast_period || 10,
      slow_period: profile.parameters.slow_period || 30,
      period: profile.parameters.period || 14,
      oversold: profile.parameters.oversold || 30,
      overbought: profile.parameters.overbought || 70,
      lookback: profile.parameters.lookback || 20,
      position_size_pct: profile.parameters.position_size_pct || 0.10
    })
  }
  
  const resetForm = () => {
    setEditingId(null)
    form.reset({
      name: '',
      strategy_type: 'sma_crossover',
      risk_max_position_pct: 0.10,
      risk_max_daily_loss_pct: 0.05,
      risk_max_concurrent_positions: 5,
      symbols: ['AAPL'],
      fast_period: 10,
      slow_period: 30,
      position_size_pct: 0.10
    })
    setActiveStrategy('sma_crossover')
  }
  
  const getStrategyFields = () => {
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
      case 'momentum_breakout':
        return [
          { name: 'lookback', label: 'Lookback Period', min: 2, max: 100, step: 1 },
          { name: 'position_size_pct', label: 'Position Size %', min: 0.01, max: 1, step: 0.01 }
        ]
    }
  }
  
  const strategyFields = getStrategyFields()
  
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Strategy Profiles</h1>
        <p className="mt-1 text-sm text-gray-500">
          Create and manage trading strategy configurations.
        </p>
      </div>
      
      {/* Create/Edit Form */}
      <div className="card">
        <h2 className="text-lg font-medium text-gray-900 mb-4">
          {editingId ? 'Edit Profile' : 'Create New Profile'}
        </h2>
        
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-800 flex items-center">
            <AlertCircle className="h-4 w-4 mr-2" />
            {error}
          </div>
        )}
        
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          {/* Basic Info */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="label">Profile Name</label>
              <input
                {...form.register('name')}
                className="input"
                placeholder="My SMA Strategy"
              />
              {form.formState.errors.name && (
                <p className="mt-1 text-sm text-red-600">{form.formState.errors.name.message}</p>
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
                        ? 'border-blue-500 bg-blue-50'
                        : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <div className="flex items-center">
                      <span className={`mr-2 ${activeStrategy === option.value ? 'text-blue-600' : 'text-gray-400'}`}>
                        {option.icon}
                      </span>
                      <div className={`h-4 w-4 rounded-full border-2 flex items-center justify-center ${
                        activeStrategy === option.value
                          ? 'border-blue-500 bg-blue-500'
                          : 'border-gray-300'
                      }`}>
                        {activeStrategy === option.value && (
                          <div className="h-2 w-2 rounded-full bg-white" />
                        )}
                      </div>
                    </div>
                    <p className="mt-1 text-xs text-gray-600">{option.label}</p>
                    <p className="text-xs text-gray-500">{option.description}</p>
                  </button>
                ))}
              </div>
            </div>
          </div>
          
          {/* Strategy-Specific Parameters */}
          <div className="bg-gray-50 rounded-lg p-4">
            <h3 className="font-medium text-gray-900 mb-4">Strategy Parameters</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {strategyFields.map(field => (
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
                    <p className="mt-1 text-sm text-red-600">{(form.formState.errors as any)[field.name].message}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
          
          {/* Risk Parameters */}
          <div className="border-t border-gray-200 pt-6">
            <h3 className="font-medium text-gray-900 mb-4">Risk Management</h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="label">Max Position Size (%)</label>
                <input
                  {...form.register('risk_max_position_pct', { valueAsNumber: true })}
                  type="number"
                  className="input"
                  min="0.01"
                  max="1"
                  step="0.01"
                />
              </div>
              <div>
                <label className="label">Max Daily Loss (%)</label>
                <input
                  {...form.register('risk_max_daily_loss_pct', { valueAsNumber: true })}
                  type="number"
                  className="input"
                  min="0.01"
                  max="1"
                  step="0.01"
                />
              </div>
              <div>
                <label className="label">Max Concurrent Positions</label>
                <input
                  {...form.register('risk_max_concurrent_positions', { valueAsNumber: true })}
                  type="number"
                  className="input"
                  min="1"
                  max="20"
                  step="1"
                />
              </div>
            </div>
          </div>
          
          {/* Symbols */}
          <div className="border-t border-gray-200 pt-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-medium text-gray-900">Trading Symbols</h3>
              <button
                type="button"
                onClick={() => appendSymbol('')}
                className="btn-secondary text-sm"
              >
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
                    className="text-gray-400 hover:text-red-600"
                  >
                    <Trash2 className="h-5 w-5" />
                  </button>
                </div>
              ))}
            </div>
            {form.formState.errors.symbols && (
              <p className="mt-1 text-sm text-red-600">{form.formState.errors.symbols.message}</p>
            )}
          </div>
          
          {/* Submit Buttons */}
          <div className="flex items-center justify-end gap-3 border-t border-gray-200 pt-4">
            {editingId && (
              <button
                type="button"
                onClick={resetForm}
                className="btn-secondary"
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              disabled={saving}
              className="btn-primary"
            >
              {saving ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : editingId ? (
                <>
                  <Save className="h-4 w-4 mr-2" />
                  Update Profile
                </>
              ) : (
                <>
                  <Save className="h-4 w-4 mr-2" />
                  Create Profile
                </>
              )}
            </button>
          </div>
        </form>
      </div>
      
      {/* Profiles List */}
      <div className="card">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Saved Profiles</h2>
        
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          </div>
        ) : profiles.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            No profiles created yet. Create your first strategy profile above.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
                  <th className="pb-3 font-medium">Name</th>
                  <th className="pb-3 font-medium">Strategy</th>
                  <th className="pb-3 font-medium">Symbols</th>
                  <th className="pb-3 font-medium">Risk</th>
                  <th className="pb-3 font-medium">Status</th>
                  <th className="pb-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {profiles.map(profile => (
                  <tr key={profile.id} className={editingId === profile.id ? 'bg-blue-50' : ''}>
                    <td className="py-3 font-medium text-gray-900">{profile.name}</td>
                    <td className="py-3">
                      <span className="badge-info">{profile.strategy_type.replace('_', ' ')}</span>
                    </td>
                    <td className="py-3 text-sm text-gray-600">
                      {profile.symbols.join(', ')}
                    </td>
                    <td className="py-3 text-sm text-gray-600">
                      Pos: {(profile.risk_max_position_pct * 100).toFixed(0)}% | 
                      Daily Loss: {(profile.risk_max_daily_loss_pct * 100).toFixed(0)}% | 
                      Max: {profile.risk_max_concurrent_positions}
                    </td>
                    <td className="py-3">
                      {profile.enabled ? (
                        <span className="badge-success">Active</span>
                      ) : (
                        <span className="badge-gray">Inactive</span>
                      )}
                    </td>
                    <td className="py-3">
                      <div className="flex items-center gap-2">
                        {!profile.enabled && (
                          <button
                            onClick={() => handleActivate(profile.id)}
                            className="btn-success text-sm"
                            title="Activate"
                          >
                            <Check className="h-4 w-4" />
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
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}