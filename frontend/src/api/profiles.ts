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
  /**
   * Opt-in sweep of idle cash into a broad instrument (issue #2). Default
   * absent, which means off - a sweep is a position like any other and the
   * operator has to ask for it.
   */
  cash_sweep?: {
    enabled?: boolean
    symbol?: string
    pct?: number
  }
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
  allow_fractional_shares: boolean
  /**
   * The most of the account this profile can actually deploy, as a fraction of
   * equity: `min(1, positions x min(risk_max_position_pct, position_size_pct))`.
   *
   * This is the answer to "why is my cash sitting idle". The live profile
   * reported 0.75 - five positions at 15% each - so a quarter of the account
   * was unreachable and nothing in the UI said so. Rendered next to the caps
   * that produce it, so the number is something you can act on.
   */
  max_deployable_pct: number
}

export interface ProfileCreate {
  name: string
  strategy_type: StrategyType
  parameters: StrategyParameters
  risk_max_position_pct: number
  risk_max_daily_loss_pct: number
  risk_max_concurrent_positions: number
  symbols: string[]
  allow_fractional_shares?: boolean
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
  allow_fractional_shares?: boolean
}

export const profilesApi = {
  list: () => api.get<Profile[]>('/profiles'),
  get: (id: number) => api.get<Profile>(`/profiles/${id}`),
  create: (data: ProfileCreate) => api.post<Profile>('/profiles', data),
  update: (id: number, data: ProfileUpdate) => api.patch<Profile>(`/profiles/${id}`, data),
  delete: (id: number) => api.delete(`/profiles/${id}`),
  activate: (id: number) => api.post<Profile>(`/profiles/${id}/activate`)
}