// Dashboard API
import { api } from './client'

export interface AccountSummary {
  status: string
  equity: number
  portfolio_value: number
  cash: number
  buying_power: number
  day_pl: number
  total_pl: number
}

export interface Position {
  symbol: string
  qty: number
  side: 'long' | 'short'
  market_value: number
  cost_basis: number
  unrealized_pl: number
  unrealized_plpc: number
  current_price: number
  avg_entry_price: number
}

// Mirrors the backend OrderResponse exactly. Note the field is `order_type`,
// not `type` - the backend names it that to stay consistent with TradeLog.
export interface Order {
  id: string
  symbol: string
  qty: number
  side: string
  order_type: string
  limit_price: number | null
  status: string
  filled_price: number | null
  filled_qty: number | null
  submitted_at: string
}

export interface EquityPoint {
  timestamp: string
  equity: number
}

export interface TradeLog {
  id: number
  timestamp: string
  profile_id: number | null
  symbol: string
  side: string
  qty: number
  order_type: string
  limit_price: number | null
  status: string
  alpaca_order_id: string | null
  filled_price: number | null
  filled_qty: number | null
  message: string
  error_details: string | null
}

export interface MarketClock {
  timestamp: string
  is_open: boolean
  next_open: string
  next_close: string
}

export const dashboardApi = {
  getAccount: () => api.get<AccountSummary>('/dashboard/account'),
  getPositions: () => api.get<Position[]>('/dashboard/positions'),
  getOrders: () => api.get<Order[]>('/dashboard/orders'),
  getEquityCurve: () => api.get<EquityPoint[]>('/dashboard/equity-curve'),
  getLogs: (limit?: number) => api.get<TradeLog[]>('/dashboard/logs', { params: { limit } }),
  getMarketClock: () => api.get<MarketClock>('/dashboard/market-clock')
}