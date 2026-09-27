// Bot Config API
import { api } from './client'

export interface BotConfig {
  id: number
  schedule_cron: string
  market_hours_only: boolean
  active_profile_id: number | null
  is_running: boolean
  created_at: string
  updated_at: string
}

export interface BotConfigUpdate {
  schedule_cron?: string
  market_hours_only?: boolean
  active_profile_id?: number | null
}

export const botConfigApi = {
  get: () => api.get<BotConfig>('/bot/config'),
  update: (data: BotConfigUpdate) => api.patch<BotConfig>('/bot/config', data),
  start: () => api.post('/bot/start'),
  stop: () => api.post('/bot/stop'),
  killSwitch: () => api.post('/bot/kill-switch')
}