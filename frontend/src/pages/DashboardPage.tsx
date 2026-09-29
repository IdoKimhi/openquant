// Dashboard Page - Real-time account data, positions, orders, equity curve
import { useState, useEffect } from 'react'
import { Loader2, RefreshCw, TrendingUp, DollarSign, Wallet, CreditCard, Activity, AlertCircle, List } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell
} from 'recharts'
import { dashboardApi, AccountSummary, Position, Order, EquityPoint, TradeLog, MarketClock } from '../api/dashboard'
import { safeFormat, safeFormatDistance, zoneLabel } from '../lib/format'
import { useChartColors } from '../lib/chartColors'

// Which window the broker is in. `is_open` alone cannot distinguish pre-market
// and after-hours from the regular session, and the bot treats all three
// differently when `market_hours_only` is on (issue #3). Kept as a lookup
// rather than inline conditionals so an unrecognised value from a future
// backend version falls back to the old open/closed reading instead of
// rendering `undefined`.
const SESSION_LABEL: Record<string, string> = {
  regular: 'OPEN (Regular)',
  pre_market: 'PRE-MARKET',
  after_hours: 'AFTER-HOURS',
  closed: 'CLOSED',
}

// A closed market is neutral; anything open is green, extended hours included.
// The distinction that matters to the bot is in the label, not the colour.
function sessionTone(clock: MarketClock): string {
  if (!clock.is_open) return 'bg-canvas border border-line'
  if (clock.session_state === 'regular') return 'bg-success-soft border border-line-success'
  // Extended hours: a warning tone, because the bot will not trade here when
  // market_hours_only is on and the user should be able to see that at a glance.
  return 'bg-warning-soft border border-line-warning'
}


export function DashboardPage() {
  // Resolved from the active theme; see lib/chartColors.ts.
  const chart = useChartColors()
  const COLORS = chart.palette
  const [account, setAccount] = useState<AccountSummary | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [equityCurve, setEquityCurve] = useState<EquityPoint[]>([])
  const [logs, setLogs] = useState<TradeLog[]>([])
  const [marketClock, setMarketClock] = useState<MarketClock | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'overview' | 'positions' | 'orders' | 'equity' | 'logs'>('overview')
  
  const loadData = async (silent = false) => {
    if (!silent) setLoading(true)
    setError(null)
    
    try {
      const [
        accountRes,
        positionsRes,
        ordersRes,
        equityRes,
        logsRes,
        clockRes
      ] = await Promise.all([
        dashboardApi.getAccount(),
        dashboardApi.getPositions(),
        dashboardApi.getOrders(),
        dashboardApi.getEquityCurve(),
        dashboardApi.getLogs(50),
        dashboardApi.getMarketClock()
      ])
      
      setAccount(accountRes.data)
      setPositions(positionsRes.data)
      setOrders(ordersRes.data)
      setEquityCurve(equityRes.data)
      setLogs(logsRes.data)
      setMarketClock(clockRes.data)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load dashboard data')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }
  
  useEffect(() => {
    loadData()
    // Auto-refresh every 30 seconds
    const interval = setInterval(() => loadData(true), 30000)
    return () => clearInterval(interval)
  }, [])
  
  const handleRefresh = () => {
    setRefreshing(true)
    loadData()
  }
  
  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(value)
  }
  
  const formatNumber = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(value)
  }
  
  const getPnLColor = (value: number) => value >= 0 ? 'text-success-fg' : 'text-danger-fg'
  const getPnLBg = (value: number) => value >= 0 ? 'bg-success-soft' : 'bg-danger-soft'

  // Every date below goes through safeFormat / safeFormatDistance from
  // lib/format.ts, because a throw inside render unmounts the page.
  //
  // The market's zone is passed explicitly rather than left to the browser's.
  // It comes from the API (`marketClock.timezone`, which is the worker's own
  // MARKET_TZ) for the same reason the Schedule page renders `cron_timezone`
  // instead of hardcoding "ET": the zone is a deployment setting, and a
  // literal in the frontend goes stale the moment it is changed. A user in
  // Jerusalem reading a New York session was the concrete failure - their
  // browser rendered a 15:30 EDT trade as 22:30 local, which is what made
  // issue #3 look like after-hours trading.
  const marketTz = marketClock?.timezone

  // Issue #1: the page header is a row of title plus button, and at 360px the
  // button has nowhere to go. Wraps instead of overflowing, and the button
  // stops shrinking its label into an ellipsis.
  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-12 w-12 animate-spin text-accent" />
      </div>
    )
  }
  
  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-body">Dashboard</h1>
          <p className="mt-1 text-sm text-muted">Real-time account and trading overview</p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="btn-secondary flex-shrink-0"
        >
          <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>
      
      {/* Error Banner */}
      {error && (
        <div className="bg-danger-soft border border-line-danger rounded-lg p-4">
          <div className="flex items-center text-danger-fg">
            <AlertCircle className="h-5 w-5 mr-2" />
            <span>{error}</span>
            <button onClick={handleRefresh} className="ml-auto text-sm underline">Retry</button>
          </div>
        </div>
      )}
      
      {/* Market Status.

          `flex-wrap` + `gap-y-1`, because this row held five elements and
          `ml-auto` on the last one assumes a single line - at 360px the
          "Updated: 2 minutes ago" was pushed off the right edge instead of
          wrapping.

          The session is named, not just open/closed (issue #3). `is_open` is
          true for the whole 04:00-20:00 ET window, so "OPEN" during pre-market
          or after-hours is true and useless; the bot is gated on the same
          distinction, and the dashboard said nothing about which window it was
          looking at. */}
      {marketClock && (
        <div className={`flex flex-wrap items-center gap-x-4 gap-y-1 p-4 rounded-lg ${
          sessionTone(marketClock)
        }`}>
          <div className={`flex items-center h-3 w-3 rounded-full flex-shrink-0 ${
            marketClock.is_open ? 'bg-success animate-pulse' : 'bg-line'
          }`} />
          <span className="font-medium text-body">
            Market: {SESSION_LABEL[marketClock.session_state] ?? (marketClock.is_open ? 'OPEN' : 'CLOSED')}
          </span>
          <span className="text-sm text-muted">
            {marketClock.is_open
              ? `Closes at ${safeFormat(marketClock.next_close, 'h:mm a', marketTz)}`
              : `Opens at ${safeFormat(marketClock.next_open, 'h:mm a', marketTz)}`}
            {marketTz && <span className="text-subtle"> {zoneLabel(marketTz)}</span>}
          </span>
          <span className="text-xs text-subtle sm:ml-auto">
            Updated: {safeFormatDistance(marketClock.timestamp)}
          </span>
        </div>
      )}
      
      {/* Account Summary Cards */}
      {account && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted">Equity</p>
                <p className="text-2xl font-bold text-body">{formatCurrency(account.equity)}</p>
              </div>
              <div className="p-3 bg-accent-soft rounded-full">
                <DollarSign className="h-6 w-6 text-accent" />
              </div>
            </div>
          </div>
          
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted">Cash</p>
                <p className="text-2xl font-bold text-body">{formatCurrency(account.cash)}</p>
              </div>
              <div className="p-3 bg-success-soft rounded-full">
                <Wallet className="h-6 w-6 text-success-fg" />
              </div>
            </div>
          </div>
          
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted">Buying Power</p>
                <p className="text-2xl font-bold text-body">{formatCurrency(account.buying_power)}</p>
              </div>
              <div className="p-3 bg-accent-soft rounded-full">
                <CreditCard className="h-6 w-6 text-accent" />
              </div>
            </div>
          </div>
          
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted">Day P&L</p>
                <p className={`text-2xl font-bold ${getPnLColor(account.day_pl)}`}>
                  {account.day_pl >= 0 ? '+' : ''}{formatCurrency(account.day_pl)}
                </p>
              </div>
              <div className={`p-3 rounded-full ${getPnLBg(account.day_pl)}`}>
                <TrendingUp className={`h-6 w-6 ${getPnLColor(account.day_pl)}`} />
              </div>
            </div>
          </div>
        </div>
      )}
      
      {/* Tabs. `overflow-x-auto` plus `scrollbar-thin` on the strip, and
          `flex-shrink-0` on each tab: five labelled tabs are ~440px of content
          in a 320px-wide card, so without a scroll container the last two
          were simply unreachable on a phone. The tab *labels* are hidden below
          sm so the icons alone fit, and the accessible name is kept via
          aria-label - a tab with no text and no label is a blank control.

          `scrollbar-thin` is applied rather than left default because
          index.css defines it and nothing used it: a default scrollbar on a
          mobile WebView overlay is wide enough to eat a tab. */}
      <div className="card">
        <div className="border-b border-line -mx-4 sm:mx-0">
          <nav className="flex -mb-px overflow-x-auto scrollbar-thin" aria-label="Dashboard sections">
            {[
              { id: 'overview', label: 'Overview', icon: Activity },
              { id: 'positions', label: 'Positions', icon: Wallet },
              { id: 'orders', label: 'Orders', icon: CreditCard },
              { id: 'equity', label: 'Equity Curve', icon: TrendingUp },
              { id: 'logs', label: 'Activity Log', icon: List }
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                aria-label={tab.label}
                aria-current={activeTab === tab.id}
                className={`flex flex-shrink-0 items-center px-3 sm:px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                  activeTab === tab.id
                    ? 'border-accent text-accent'
                    : 'border-transparent text-muted hover:text-body hover:border-line-strong'
                }`}
              >
                <tab.icon className="h-4 w-4 sm:mr-2" />
                {/* Icon-only below sm; the aria-label above carries the name. */}
                <span className="hidden sm:inline ml-0">{tab.label}</span>
              </button>
            ))}
          </nav>
        </div>
        
        {/* `min-w-0` so the wide tables inside can shrink and hand the overflow
            to their own scroll container. A flex/grid child defaults to
            `min-width: auto`, which refuses to shrink below its content and is
            what pushes the whole page sideways. */}
        <div className="p-0 sm:p-4 min-w-0">
          {activeTab === 'overview' && account && (
            <div className="space-y-6">
              {/* Portfolio Allocation */}
              {positions.length > 0 && (
                <div>
                  <h3 className="text-lg font-medium text-body mb-4">Portfolio Allocation</h3>
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 min-w-0">
                    <div className="h-64 min-w-0">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie
                            data={positions.map(p => ({
                              name: p.symbol,
                              value: Math.abs(p.market_value)
                            }))}
                            cx="50%"
                            cy="50%"
                            innerRadius={60}
                            outerRadius={100}
                            fill={COLORS[0]}
                            paddingAngle={2}
                            dataKey="value"
                            label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                          >
                            {positions.map((_, index) => (
                              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                            ))}
                          </Pie>
                          <Tooltip formatter={(value: number) => [formatCurrency(value), 'Value']} />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                    <div className="space-y-3">
                      {positions.map((pos, index) => (
                        <div key={pos.symbol} className="flex items-center justify-between p-3 bg-canvas rounded-lg">
                          <div className="flex items-center">
                            <div className={`h-3 w-3 rounded-full mr-3`} style={{ backgroundColor: COLORS[index % COLORS.length] }} />
                            <div>
                              <p className="font-medium text-body">{pos.symbol}</p>
                              <p className="text-sm text-muted">{pos.qty} shares @ ${formatNumber(pos.avg_entry_price)}</p>
                            </div>
                          </div>
                          <div className="text-right">
                            <p className={`font-medium ${getPnLColor(pos.unrealized_pl)}`}>
                              {pos.unrealized_pl >= 0 ? '+' : ''}{formatCurrency(pos.unrealized_pl)}
                            </p>
                            <p className="text-sm text-muted">
                              {pos.unrealized_plpc >= 0 ? '+' : ''}{formatNumber(pos.unrealized_plpc * 100)}%
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
              
              {/* Quick Stats */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="p-4 bg-canvas rounded-lg">
                  <p className="text-sm text-muted">Total P&L</p>
                  <p className={`text-2xl font-bold ${getPnLColor(account.total_pl)}`}>
                    {account.total_pl >= 0 ? '+' : ''}{formatCurrency(account.total_pl)}
                  </p>
                </div>
                <div className="p-4 bg-canvas rounded-lg">
                  <p className="text-sm text-muted">Portfolio Value</p>
                  <p className="text-2xl font-bold text-body">{formatCurrency(account.portfolio_value)}</p>
                </div>
                <div className="p-4 bg-canvas rounded-lg">
                  <p className="text-sm text-muted">Open Positions</p>
                  <p className="text-2xl font-bold text-body">{positions.length}</p>
                </div>
              </div>
            </div>
          )}
          
          {activeTab === 'positions' && (
            <div>
              {positions.length === 0 ? (
                <div className="text-center py-12 px-4">
                  <Wallet className="h-12 w-12 text-subtle mx-auto mb-4" />
                  <p className="text-muted">No open positions</p>
                </div>
              ) : (
                <div className="overflow-x-auto scrollbar-thin">
                  {/* min-w forces the table to keep its natural width and the
                      wrapper to scroll. `w-full` alone lets a table compress
                      until the cell text wraps to one character per line, which
                      is worse than a scrollbar: the columns stop being
                                      comparable at a glance. */}
                  <table className="w-full min-w-[640px]">
                    <thead>
                      <tr className="text-left text-sm text-muted border-b border-line">
                        <th className="pb-3 font-medium">Symbol</th>
                        <th className="pb-3 font-medium">Qty</th>
                        <th className="pb-3 font-medium">Side</th>
                        <th className="pb-3 font-medium">Avg Entry</th>
                        <th className="pb-3 font-medium">Current</th>
                        <th className="pb-3 font-medium">Market Value</th>
                        <th className="pb-3 font-medium">Unrealized P&L</th>
                        <th className="pb-3 font-medium">P&L %</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {positions.map(pos => (
                        <tr key={pos.symbol} className="hover:bg-canvas">
                          <td className="py-3 font-medium text-body">{pos.symbol}</td>
                          <td className="py-3 text-muted">{pos.qty}</td>
                          <td className="py-3">
                            <span className={`badge ${pos.side === 'long' ? 'badge-success' : 'badge-danger'}`}>
                              {pos.side}
                            </span>
                          </td>
                          <td className="py-3 text-muted">{formatCurrency(pos.avg_entry_price)}</td>
                          <td className="py-3 text-muted">{formatCurrency(pos.current_price)}</td>
                          <td className="py-3 font-medium text-body">{formatCurrency(pos.market_value)}</td>
                          <td className={`py-3 font-medium ${getPnLColor(pos.unrealized_pl)}`}>
                            {pos.unrealized_pl >= 0 ? '+' : ''}{formatCurrency(pos.unrealized_pl)}
                          </td>
                          <td className={`py-3 font-medium ${getPnLColor(pos.unrealized_plpc)}`}>
                            {pos.unrealized_plpc >= 0 ? '+' : ''}{formatNumber(pos.unrealized_plpc * 100)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          
          {activeTab === 'orders' && (
            <div>
              {orders.length === 0 ? (
                <div className="text-center py-12 px-4">
                  <CreditCard className="h-12 w-12 text-subtle mx-auto mb-4" />
                  <p className="text-muted">No recent orders</p>
                </div>
              ) : (
                <div className="overflow-x-auto scrollbar-thin">
                  {/* min-w forces the table to keep its natural width and the
                      wrapper to scroll. `w-full` alone lets a table compress
                      until the cell text wraps to one character per line, which
                      is worse than a scrollbar: the columns stop being
                                      comparable at a glance. */}
                  <table className="w-full min-w-[640px]">
                    <thead>
                      <tr className="text-left text-sm text-muted border-b border-line">
                        <th className="pb-3 font-medium">Time</th>
                        <th className="pb-3 font-medium">Symbol</th>
                        <th className="pb-3 font-medium">Side</th>
                        <th className="pb-3 font-medium">Type</th>
                        <th className="pb-3 font-medium">Qty</th>
                        <th className="pb-3 font-medium">Price</th>
                        <th className="pb-3 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {orders.slice(0, 20).map(order => (
                        <tr key={order.id} className="hover:bg-canvas">
                          <td className="py-3 text-sm text-muted whitespace-nowrap">
                            {safeFormat(order.submitted_at, 'MMM d, h:mm a', marketTz)}
                          </td>
                          <td className="py-3 font-medium text-body">{order.symbol}</td>
                          <td className="py-3">
                            <span className={`badge ${order.side === 'buy' ? 'badge-success' : 'badge-danger'}`}>
                              {order.side.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-3 text-muted capitalize">{order.order_type}</td>
                          <td className="py-3 text-muted">{order.qty}</td>
                          <td className="py-3 text-muted">
                            {order.limit_price ? formatCurrency(order.limit_price) : 'Market'}
                          </td>
                          <td className="py-3">
                            <span className={`badge ${
                              order.status === 'filled' ? 'badge-success' :
                              order.status === 'canceled' ? 'badge-gray' :
                              order.status === 'rejected' ? 'badge-danger' :
                              order.status === 'pending_new' || order.status === 'new' ? 'badge-info' :
                              'badge-warning'
                            }`}>
                              {order.status.replace('_', ' ')}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          
          {activeTab === 'equity' && (
            <div>
              {equityCurve.length === 0 ? (
                <div className="text-center py-12 px-4">
                  <TrendingUp className="h-12 w-12 text-subtle mx-auto mb-4" />
                  <p className="text-muted">No equity data yet</p>
                  <p className="text-sm text-subtle mt-1">Equity curve will appear after the bot runs</p>
                </div>
              ) : (
                <div className="px-4 sm:px-0">
                  {/* The axis is labelled with the market's zone (issue #4).
                      The x values are UTC instants and the y is the account, so
                      a chart whose axis is silently in the viewer's zone
                      shows a session boundary at the wrong hour - which is how
                      a 19:30 UTC trade came to be read as an after-hours one.
                      `min-w-0` because ResponsiveContainer measures its
                      parent, and a flex child that refuses to shrink makes it
                      render at 0 width. */}
                  <p className="text-xs text-subtle mb-2">
                    Times shown in {marketTz ? zoneLabel(marketTz) : 'the market'} time
                  </p>
                  <div className="h-64 sm:h-80 min-w-0">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={equityCurve}>
                      <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} />
                      <XAxis
                        dataKey="timestamp"
                        tickFormatter={value => safeFormat(value, 'MMM d', marketTz)}
                        tick={{ fill: chart.tick, fontSize: 12 }}
                        stroke={chart.grid}
                        minTickGap={16}
                      />
                      <YAxis
                        tickFormatter={value => formatCurrency(value)}
                        tick={{ fill: chart.tick, fontSize: 12 }}
                        stroke={chart.grid}
                        width={64}
                      />
                      <Tooltip
                        formatter={(value: number) => [formatCurrency(value), 'Equity']}
                        labelFormatter={value => safeFormat(value, 'MMM d, h:mm a', marketTz)}
                      />
                      <Line
                        type="monotone"
                        dataKey="equity"
                        stroke={COLORS[0]}
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 6 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                  </div>
                </div>
              )}
            </div>
          )}
          
          {activeTab === 'logs' && (
            <div>
              {logs.length === 0 ? (
                <div className="text-center py-12 px-4">
                  <Activity className="h-12 w-12 text-subtle mx-auto mb-4" />
                  <p className="text-muted">No trading activity yet</p>
                </div>
              ) : (
                <div className="overflow-x-auto scrollbar-thin">
                  {/* min-w forces the table to keep its natural width and the
                      wrapper to scroll. `w-full` alone lets a table compress
                      until the cell text wraps to one character per line, which
                      is worse than a scrollbar: the columns stop being
                                      comparable at a glance. */}
                  <table className="w-full min-w-[640px]">
                    <thead>
                      <tr className="text-left text-sm text-muted border-b border-line">
                        <th className="pb-3 font-medium">Time</th>
                        <th className="pb-3 font-medium">Symbol</th>
                        <th className="pb-3 font-medium">Side</th>
                        <th className="pb-3 font-medium">Qty</th>
                        <th className="pb-3 font-medium">Filled</th>
                        <th className="pb-3 font-medium">Type</th>
                        <th className="pb-3 font-medium">Price</th>
                        <th className="pb-3 font-medium">Status</th>
                        <th className="pb-3 font-medium">Message</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {logs.map(log => (
                        <tr key={log.id} className="hover:bg-canvas">
                          {/* Market time, not browser time - see marketTz. */}
                          <td className="py-3 text-sm text-muted whitespace-nowrap">
                            {safeFormat(log.timestamp, 'MMM d, h:mm:ss a', marketTz)}
                          </td>
                          <td className="py-3 font-medium text-body">{log.symbol}</td>
                          <td className="py-3">
                            <span className={`badge ${log.side === 'buy' ? 'badge-success' : 'badge-danger'}`}>
                              {log.side.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-3 text-muted">{log.qty}</td>
                          {/* What the bot actually got (issue #5).

                              The two columns that were the point of that issue:
                              `filled_qty` against the ordered `qty` shows a
                              partial, and `filled_price` is the execution price
                              rather than the order's limit. Before the fix
                              every row here was blank, because the worker wrote
                              the row the moment the order was accepted and
                              never went back for the fill.

                              A dash, not a zero - "not filled yet" and "filled
                              at nothing" are different facts, and 0.00 would
                              read as a data error. */}
                          <td className="py-3 text-muted whitespace-nowrap">
                            {log.filled_qty !== null && log.filled_qty !== undefined
                              ? `${formatNumber(log.filled_qty)} @ ${
                                  log.filled_price !== null && log.filled_price !== undefined
                                    ? formatCurrency(log.filled_price)
                                    : '-'
                                }`
                              : '-'}
                          </td>
                          <td className="py-3 text-muted capitalize">{log.order_type}</td>
                          <td className="py-3 text-muted">
                            {log.limit_price ? formatCurrency(log.limit_price) : 'Market'}
                          </td>
                          <td className="py-3">
                            <span className={`badge ${
                              log.status === 'filled' ? 'badge-success' :
                              log.status === 'error' ? 'badge-danger' :
                              log.status === 'canceled' ? 'badge-gray' :
                              'badge-info'
                            }`}>
                              {log.status}
                            </span>
                          </td>
                          <td className="py-3 text-sm text-muted max-w-[16rem] truncate">{log.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}