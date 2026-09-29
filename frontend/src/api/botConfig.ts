// Bot Config API
import { api } from './client'

export interface BotConfig {
  // No `id` or `created_at`: BotConfigResponse in app/schemas.py has neither -
  // bot_config is a single row and the UI would have nothing to do with its
  // id or creation time. They were declared here once, the API never sent
  // them, and nothing read them - the exact dormant shape of gotcha 3's
  // drift, where a field that has never been exercised is read for the same
  // reason it was declared, and `undefined` renders until a date throws.
  schedule_cron: string
  market_hours_only: boolean
  active_profile_id: number | null
  is_running: boolean
  updated_at: string
  /**
   * IANA zone the worker evaluates the cron expression in - the backend's
   * BOT_TIMEZONE, i.e. exactly what it hands to APScheduler. Render it rather
   * than hardcoding "ET": this page used to claim UTC, which is true of
   * APScheduler's default and false of this app, so a "9-16" schedule written
   * against the label fired 05:00-12:00 Eastern.
   */
  cron_timezone: string
  /**
   * The share of account equity the strategies may size positions against
   * (issue #6, the "money to invest" control). 1.0 is the whole account; 0.8
   * holds a 20% cash reserve. Never null - the backend normalises a row written
   * before the column existed, and null here renders as a blank field.
   */
  capital_allocation_pct: number
}

export interface BotConfigUpdate {
  schedule_cron?: string
  market_hours_only?: boolean
  active_profile_id?: number | null
  capital_allocation_pct?: number
}

export const botConfigApi = {
  get: () => api.get<BotConfig>('/bot/config'),
  update: (data: BotConfigUpdate) => api.patch<BotConfig>('/bot/config', data),
  start: () => api.post('/bot/start'),
  stop: () => api.post('/bot/stop'),
  killSwitch: () => api.post('/bot/kill-switch')
}