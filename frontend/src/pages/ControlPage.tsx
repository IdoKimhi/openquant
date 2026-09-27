// Control Page - Start/stop bot, kill switch
import { useState, useEffect } from 'react'
import { AlertCircle, CheckCircle, Loader2, Play, Square, AlertTriangle, Zap, Shield } from 'lucide-react'
import { botConfigApi, BotConfig } from '../api/botConfig'

export function ControlPage() {
  const [config, setConfig] = useState<BotConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [showKillConfirm, setShowKillConfirm] = useState(false)
  
  const loadConfig = async () => {
    try {
      const response = await botConfigApi.get()
      setConfig(response.data)
    } catch (err) {
      setError('Failed to load bot status')
    } finally {
      setLoading(false)
    }
  }
  
  useEffect(() => {
    loadConfig()
    // Poll for status updates
    const interval = setInterval(loadConfig, 5000)
    return () => clearInterval(interval)
  }, [])
  
  const handleAction = async (action: 'start' | 'stop' | 'kill') => {
    setActionLoading(action)
    setError(null)
    setSuccess(null)
    
    try {
      if (action === 'start') {
        await botConfigApi.start()
        setSuccess('Bot started successfully!')
      } else if (action === 'stop') {
        await botConfigApi.stop()
        setSuccess('Bot stopped successfully!')
      } else if (action === 'kill') {
        await botConfigApi.killSwitch()
        setSuccess('Kill switch triggered! All positions being closed and orders cancelled.')
      }
      loadConfig()
    } catch (err: any) {
      setError(err.response?.data?.detail || `Failed to ${action} bot`)
    } finally {
      setActionLoading(null)
      setShowKillConfirm(false)
    }
  }
  
  const canStart = config && !config.is_running && config.active_profile_id
  const canStop = config && config.is_running
  const canKill = config && config.is_running
  
  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Bot Control</h1>
        <p className="mt-1 text-sm text-gray-500">
          Start, stop, or emergency stop the trading bot.
        </p>
      </div>
      
      {/* Status Card */}
      <div className="card">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Bot Status</h2>
        
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          </div>
        ) : config ? (
          <div className="space-y-4">
            {/* Main Status */}
            <div className={`p-4 rounded-lg ${
              config.is_running ? 'bg-green-50 border border-green-200' : 'bg-gray-50 border border-gray-200'
            }`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center">
                  <div className={`h-4 w-4 rounded-full ${
                    config.is_running ? 'bg-green-500' : 'bg-gray-300'
                  } animate-pulse`} />
                  <span className="ml-3 text-lg font-medium text-gray-900">
                    {config.is_running ? 'RUNNING' : 'STOPPED'}
                  </span>
                </div>
                <span className={`badge ${config.is_running ? 'badge-success' : 'badge-gray'} text-lg px-4 py-2`}>
                  {config.is_running ? (
                    <>
                      <Zap className="h-4 w-4 mr-1" />
                      Active
                    </>
                  ) : (
                    <>
                      <Square className="h-4 w-4 mr-1" />
                      Inactive
                    </>
                  )}
                </span>
              </div>
              
              <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
                <div>
                  <dt className="text-gray-500">Active Profile</dt>
                  <dd className="font-medium text-gray-900">
                    {config.active_profile_id ? 'Configured' : 'Not set'}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Schedule</dt>
                  <dd className="font-mono text-gray-900">{config.schedule_cron}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Market Hours Only</dt>
                  <dd className="font-medium text-gray-900">
                    {config.market_hours_only ? 'Yes' : 'No'}
                  </dd>
                </div>
              </div>
            </div>
            
            {/* Prerequisites Check */}
            {!config.is_running && (
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <h3 className="font-medium text-blue-800 mb-2 flex items-center">
                  <AlertCircle className="h-5 w-5 mr-2" />
                  Prerequisites for Starting
                </h3>
                <ul className="text-sm text-blue-700 space-y-1">
                  <li className="flex items-center">
                    {config.active_profile_id ? (
                      <CheckCircle className="h-4 w-4 text-green-500 mr-2" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-red-500 mr-2" />
                    )}
                    <span className={config.active_profile_id ? 'text-green-700' : 'text-red-700'}>
                      {config.active_profile_id ? 'Active profile selected' : 'No active profile selected'}
                    </span>
                  </li>
                  <li className="flex items-center">
                    {config.schedule_cron ? (
                      <CheckCircle className="h-4 w-4 text-green-500 mr-2" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-red-500 mr-2" />
                    )}
                    <span className="text-green-700">Schedule configured</span>
                  </li>
                </ul>
              </div>
            )}
          </div>
        ) : null}
      </div>
      
      {/* Control Buttons */}
      <div className="card">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Controls</h2>
        
        {(error || success) && (
          <div className={`mb-4 p-3 rounded-md text-sm flex items-center ${
            error ? 'bg-red-50 border border-red-200 text-red-800' :
            'bg-green-50 border border-green-200 text-green-800'
          }`}>
            {error && <AlertCircle className="h-4 w-4 mr-2" />}
            {success && <CheckCircle className="h-4 w-4 mr-2" />}
            {error || success}
          </div>
        )}
        
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Start Button */}
          <button
            onClick={() => handleAction('start')}
            disabled={actionLoading !== null || !canStart}
            className={`btn-success py-6 text-lg font-medium flex flex-col items-center ${
              canStart ? '' : 'opacity-50 cursor-not-allowed'
            }`}
          >
            <Play className="h-8 w-8 mb-2" />
            <span>Start Bot</span>
            {actionLoading === 'start' && <Loader2 className="h-5 w-5 animate-spin mt-2" />}
          </button>
          
          {/* Stop Button */}
          <button
            onClick={() => handleAction('stop')}
            disabled={actionLoading !== null || !canStop}
            className={`btn-secondary py-6 text-lg font-medium flex flex-col items-center ${
              canStop ? '' : 'opacity-50 cursor-not-allowed'
            }`}
          >
            <Square className="h-8 w-8 mb-2" />
            <span>Stop Bot</span>
            {actionLoading === 'stop' && <Loader2 className="h-5 w-5 animate-spin mt-2" />}
          </button>
          
          {/* Kill Switch */}
          <button
            onClick={() => setShowKillConfirm(true)}
            disabled={actionLoading !== null || !canKill}
            className={`btn-danger py-6 text-lg font-medium flex flex-col items-center ${
              canKill ? '' : 'opacity-50 cursor-not-allowed'
            }`}
          >
            <AlertTriangle className="h-8 w-8 mb-2" />
            <span>Kill Switch</span>
            {actionLoading === 'kill' && <Loader2 className="h-5 w-5 animate-spin mt-2" />}
          </button>
        </div>
        
        {/* Action Descriptions */}
        <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-4 text-sm text-gray-600">
          <div className="p-3 bg-green-50 rounded-lg">
            <h4 className="font-medium text-green-800 mb-1 flex items-center">
              <Play className="h-4 w-4 mr-1" />
              Start Bot
            </h4>
            <p>Begin trading using the active strategy profile on the configured schedule.</p>
          </div>
          <div className="p-3 bg-gray-50 rounded-lg">
            <h4 className="font-medium text-gray-800 mb-1 flex items-center">
              <Square className="h-4 w-4 mr-1" />
              Stop Bot
            </h4>
            <p>Gracefully stop the bot. Current positions remain open; no new trades.</p>
          </div>
          <div className="p-3 bg-red-50 rounded-lg">
            <h4 className="font-medium text-red-800 mb-1 flex items-center">
              <AlertTriangle className="h-4 w-4 mr-1" />
              Kill Switch
            </h4>
            <p>Emergency stop: cancels ALL open orders and flattens ALL positions immediately.</p>
          </div>
        </div>
      </div>
      
      {/* Kill Switch Confirmation Modal */}
      {showKillConfirm && (
        <div className="fixed inset-0 z-50 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <div className="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" onClick={() => setShowKillConfirm(false)} />
            <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full p-6">
              <div className="flex items-center justify-center w-12 h-12 mx-auto mb-4 bg-red-100 rounded-full">
                <AlertTriangle className="h-7 w-7 text-red-600" />
              </div>
              <h3 className="text-lg font-medium text-gray-900 text-center mb-2">Confirm Kill Switch</h3>
              <p className="text-sm text-gray-500 text-center mb-6">
                This will immediately cancel all open orders and close all positions. This action cannot be undone.
              </p>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowKillConfirm(false)}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
                <button
                  onClick={() => handleAction('kill')}
                  disabled={actionLoading === 'kill'}
                  className="btn-danger flex-1"
                >
                  {actionLoading === 'kill' ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Executing...
                    </>
                  ) : (
                    'Confirm Kill Switch'
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      
      {/* Safety Info */}
      <div className="card bg-gray-50 border-gray-200">
        <h3 className="font-medium text-gray-900 mb-3 flex items-center">
          <Shield className="h-5 w-5 mr-2 text-gray-600" />
          Safety Features
        </h3>
        <ul className="text-sm text-gray-600 space-y-2">
          <li className="flex items-start">
            <CheckCircle className="h-5 w-5 text-green-500 mr-2 mt-0.5 flex-shrink-0" />
            <span>Paper trading only — no real money at risk</span>
          </li>
          <li className="flex items-start">
            <CheckCircle className="h-5 w-5 text-green-500 mr-2 mt-0.5 flex-shrink-0" />
            <span>Risk limits enforced: max position size, daily loss, concurrent positions</span>
          </li>
          <li className="flex items-start">
            <CheckCircle className="h-5 w-5 text-green-500 mr-2 mt-0.5 flex-shrink-0" />
            <span>Kill switch: one-click emergency flatten all positions</span>
          </li>
          <li className="flex items-start">
            <CheckCircle className="h-5 w-5 text-green-500 mr-2 mt-0.5 flex-shrink-0" />
            <span>Market hours enforcement: only trades during US market hours (optional)</span>
          </li>
          <li className="flex items-start">
            <CheckCircle className="h-5 w-5 text-green-500 mr-2 mt-0.5 flex-shrink-0" />
            <span>Encrypted API credentials at rest (Fernet/AES-128-GCM)</span>
          </li>
        </ul>
      </div>
    </div>
  )
}