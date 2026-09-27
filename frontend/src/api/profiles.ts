// Profiles API
import { api } from './client'

export type StrategyType = 'sma_crossover' | 'rsi_reversion' | 'momentum_breakout'

export interface StrategyParameters {
  // SMA Crossover
  fast_period?: number
  slow_period?: number
  // RSI Reversion
  period?: number
  oversold?: number
  overbought?: number
  // Momentum Breakout
  lookback?: number
  // Common
  position_size_pct?: number
}

export interface Profile {
  id: number
  name: string
  strategy_type: StrategyType
  parameters: StrategyParameters
  risk_max_position_pct: number
  risk_max_daily_loss_pct: number
  risk_max_concurrent_positions: number
  symbols: string[]
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface ProfileCreate {
  name: string
  strategy_type: StrategyType
  parameters: StrategyParameters
  risk_max_position_pct: number
  risk_max_daily_loss_pct: number
  risk_max_concurrent_positions: number
  symbols: string[]
}

export interface ProfileUpdate {
  name?: string
  strategy_type?: StrategyType
  parameters?: StrategyParameters
  risk_max_position_pct?: number
  risk_max_daily_loss_pct?: number
  risk_max_concurrent_positions?: number
  symbols?: string[]
  enabled?: boolean
}

export const profilesApi = {
  list: () => api.get<Profile[]>('/profiles'),
  get: (id: number) => api.get<Profile>(`/profiles/${id}`),
  create: (data: ProfileCreate) => api.post<Profile>('/profiles', data),
  update: (id: number, data: ProfileUpdate) => api.patch<Profile>(`/profiles/${id}`, data),
  delete: (id: number) => api.delete(`/profiles/${id}`),
  activate: (id: number) => api.post<Profile>(`/profiles/${id}/activate`)
}