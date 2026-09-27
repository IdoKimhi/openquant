// Dashboard Page - Real-time account data, positions, orders, equity curve
import { useState, useEffect } from 'react'
import { Loader2, RefreshCw, TrendingUp, DollarSign, Wallet, CreditCard, Activity, AlertCircle, List } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell
} from 'recharts'
import { format, formatDistanceToNow } from 'date-fns'
import { dashboardApi, AccountSummary, Position, Order, EquityPoint, TradeLog, MarketClock } from '../api/dashboard'

const COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899']

export function DashboardPage() {
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
  
  const getPnLColor = (value: number) => value >= 0 ? 'text-green-600' : 'text-red-600'
  const getPnLBg = (value: number) => value >= 0 ? 'bg-green-50' : 'bg-red-50'
  
  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-12 w-12 animate-spin text-blue-600" />
      </div>
    )
  }
  
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="mt-1 text-sm text-gray-500">Real-time account and trading overview</p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="btn-secondary"
        >
          <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>
      
      {/* Error Banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <div className="flex items-center text-red-800">
            <AlertCircle className="h-5 w-5 mr-2" />
            <span>{error}</span>
            <button onClick={handleRefresh} className="ml-auto text-sm underline">Retry</button>
          </div>
        </div>
      )}
      
      {/* Market Status */}
      {marketClock && (
        <div className={`flex items-center gap-4 p-4 rounded-lg ${
          marketClock.is_open ? 'bg-green-50 border border-green-200' : 'bg-gray-50 border border-gray-200'
        }`}>
          <div className={`flex items-center h-3 w-3 rounded-full ${marketClock.is_open ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`} />
          <span className="font-medium text-gray-900">
            Market: {marketClock.is_open ? 'OPEN' : 'CLOSED'}
          </span>
          <span className="text-sm text-gray-500">
            {marketClock.is_open
              ? `Closes at ${format(new Date(marketClock.next_close), 'h:mm a')}`
              : `Opens at ${format(new Date(marketClock.next_open), 'h:mm a')}`}
          </span>
          <span className="text-xs text-gray-400 ml-auto">
            Updated: {formatDistanceToNow(new Date(marketClock.timestamp), { addSuffix: true })}
          </span>
        </div>
      )}
      
      {/* Account Summary Cards */}
      {account && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-500">Equity</p>
                <p className="text-2xl font-bold text-gray-900">{formatCurrency(account.equity)}</p>
              </div>
              <div className="p-3 bg-blue-100 rounded-full">
                <DollarSign className="h-6 w-6 text-blue-600" />
              </div>
            </div>
          </div>
          
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-500">Cash</p>
                <p className="text-2xl font-bold text-gray-900">{formatCurrency(account.cash)}</p>
              </div>
              <div className="p-3 bg-green-100 rounded-full">
                <Wallet className="h-6 w-6 text-green-600" />
              </div>
            </div>
          </div>
          
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-500">Buying Power</p>
                <p className="text-2xl font-bold text-gray-900">{formatCurrency(account.buying_power)}</p>
              </div>
              <div className="p-3 bg-purple-100 rounded-full">
                <CreditCard className="h-6 w-6 text-purple-600" />
              </div>
            </div>
          </div>
          
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-500">Day P&L</p>
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
      
      {/* Tabs */}
      <div className="card">
        <div className="border-b border-gray-200">
          <nav className="flex -mb-px" aria-label="Tabs">
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
                className={`flex items-center px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab.id
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <tab.icon className="h-4 w-4 mr-2" />
                {tab.label}
              </button>
            ))}
          </nav>
        </div>
        
        <div className="p-4">
          {activeTab === 'overview' && account && (
            <div className="space-y-6">
              {/* Portfolio Allocation */}
              {positions.length > 0 && (
                <div>
                  <h3 className="text-lg font-medium text-gray-900 mb-4">Portfolio Allocation</h3>
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div className="h-64">
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
                            fill="#8884d8"
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
                        <div key={pos.symbol} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                          <div className="flex items-center">
                            <div className={`h-3 w-3 rounded-full mr-3`} style={{ backgroundColor: COLORS[index % COLORS.length] }} />
                            <div>
                              <p className="font-medium text-gray-900">{pos.symbol}</p>
                              <p className="text-sm text-gray-500">{pos.qty} shares @ ${formatNumber(pos.avg_entry_price)}</p>
                            </div>
                          </div>
                          <div className="text-right">
                            <p className={`font-medium ${getPnLColor(pos.unrealized_pl)}`}>
                              {pos.unrealized_pl >= 0 ? '+' : ''}{formatCurrency(pos.unrealized_pl)}
                            </p>
                            <p className="text-sm text-gray-500">
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
                <div className="p-4 bg-gray-50 rounded-lg">
                  <p className="text-sm text-gray-500">Total P&L</p>
                  <p className={`text-2xl font-bold ${getPnLColor(account.total_pl)}`}>
                    {account.total_pl >= 0 ? '+' : ''}{formatCurrency(account.total_pl)}
                  </p>
                </div>
                <div className="p-4 bg-gray-50 rounded-lg">
                  <p className="text-sm text-gray-500">Portfolio Value</p>
                  <p className="text-2xl font-bold text-gray-900">{formatCurrency(account.portfolio_value)}</p>
                </div>
                <div className="p-4 bg-gray-50 rounded-lg">
                  <p className="text-sm text-gray-500">Open Positions</p>
                  <p className="text-2xl font-bold text-gray-900">{positions.length}</p>
                </div>
              </div>
            </div>
          )}
          
          {activeTab === 'positions' && (
            <div>
              {positions.length === 0 ? (
                <div className="text-center py-12">
                  <Wallet className="h-12 w-12 text-gray-300 mx-auto mb-4" />
                  <p className="text-gray-500">No open positions</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
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
                    <tbody className="divide-y divide-gray-100">
                      {positions.map(pos => (
                        <tr key={pos.symbol} className="hover:bg-gray-50">
                          <td className="py-3 font-medium text-gray-900">{pos.symbol}</td>
                          <td className="py-3 text-gray-600">{pos.qty}</td>
                          <td className="py-3">
                            <span className={`badge ${pos.side === 'long' ? 'badge-success' : 'badge-danger'}`}>
                              {pos.side}
                            </span>
                          </td>
                          <td className="py-3 text-gray-600">{formatCurrency(pos.avg_entry_price)}</td>
                          <td className="py-3 text-gray-600">{formatCurrency(pos.current_price)}</td>
                          <td className="py-3 font-medium text-gray-900">{formatCurrency(pos.market_value)}</td>
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
                <div className="text-center py-12">
                  <CreditCard className="h-12 w-12 text-gray-300 mx-auto mb-4" />
                  <p className="text-gray-500">No recent orders</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
                        <th className="pb-3 font-medium">Time</th>
                        <th className="pb-3 font-medium">Symbol</th>
                        <th className="pb-3 font-medium">Side</th>
                        <th className="pb-3 font-medium">Type</th>
                        <th className="pb-3 font-medium">Qty</th>
                        <th className="pb-3 font-medium">Price</th>
                        <th className="pb-3 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {orders.slice(0, 20).map(order => (
                        <tr key={order.id} className="hover:bg-gray-50">
                          <td className="py-3 text-sm text-gray-600">
                            {format(new Date(order.submitted_at), 'MMM d, h:mm a')}
                          </td>
                          <td className="py-3 font-medium text-gray-900">{order.symbol}</td>
                          <td className="py-3">
                            <span className={`badge ${order.side === 'buy' ? 'badge-success' : 'badge-danger'}`}>
                              {order.side.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-3 text-gray-600 capitalize">{order.order_type}</td>
                          <td className="py-3 text-gray-600">{order.qty}</td>
                          <td className="py-3 text-gray-600">
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
                <div className="text-center py-12">
                  <TrendingUp className="h-12 w-12 text-gray-300 mx-auto mb-4" />
                  <p className="text-gray-500">No equity data yet</p>
                  <p className="text-sm text-gray-400 mt-1">Equity curve will appear after the bot runs</p>
                </div>
              ) : (
                <div className="h-80">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={equityCurve}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                      <XAxis
                        dataKey="timestamp"
                        tickFormatter={value => format(new Date(value), 'MMM d')}
                        tick={{ fill: '#6B7280', fontSize: 12 }}
                        stroke="#E5E7EB"
                      />
                      <YAxis
                        tickFormatter={value => formatCurrency(value)}
                        tick={{ fill: '#6B7280', fontSize: 12 }}
                        stroke="#E5E7EB"
                      />
                      <Tooltip
                        formatter={(value: number) => [formatCurrency(value), 'Equity']}
                        labelFormatter={value => format(new Date(value), 'MMM d, h:mm a')}
                      />
                      <Line
                        type="monotone"
                        dataKey="equity"
                        stroke="#3B82F6"
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 6 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          )}
          
          {activeTab === 'logs' && (
            <div>
              {logs.length === 0 ? (
                <div className="text-center py-12">
                  <Activity className="h-12 w-12 text-gray-300 mx-auto mb-4" />
                  <p className="text-gray-500">No trading activity yet</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
                        <th className="pb-3 font-medium">Time</th>
                        <th className="pb-3 font-medium">Symbol</th>
                        <th className="pb-3 font-medium">Side</th>
                        <th className="pb-3 font-medium">Qty</th>
                        <th className="pb-3 font-medium">Type</th>
                        <th className="pb-3 font-medium">Price</th>
                        <th className="pb-3 font-medium">Status</th>
                        <th className="pb-3 font-medium">Message</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {logs.map(log => (
                        <tr key={log.id} className="hover:bg-gray-50">
                          <td className="py-3 text-sm text-gray-600">
                            {format(new Date(log.timestamp), 'MMM d, h:mm:ss a')}
                          </td>
                          <td className="py-3 font-medium text-gray-900">{log.symbol}</td>
                          <td className="py-3">
                            <span className={`badge ${log.side === 'buy' ? 'badge-success' : 'badge-danger'}`}>
                              {log.side.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-3 text-gray-600">{log.qty}</td>
                          <td className="py-3 text-gray-600 capitalize">{log.order_type}</td>
                          <td className="py-3 text-gray-600">
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
                          <td className="py-3 text-sm text-gray-600 max-w-xs truncate">{log.message}</td>
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