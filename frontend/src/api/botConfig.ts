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
  /**
   * IANA zone the worker evaluates the cron expression in - the backend's
   * BOT_TIMEZONE, i.e. exactly what it hands to APScheduler. Render it rather
   * than hardcoding "ET": this page used to claim UTC, which is true of
   * APScheduler's default and false of this app, so a "9-16" schedule written
   * against the label fired 05:00-12:00 Eastern.
   */
  cron_timezone: string
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