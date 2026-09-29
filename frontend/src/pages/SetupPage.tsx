// Setup Page - Enter Alpaca API credentials
import React from 'react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Save, CheckCircle, AlertCircle, Loader2, Key } from 'lucide-react'
import { credentialsApi, TestConnectionResult } from '../api/credentials'

const credentialsSchema = z.object({
  key_id: z.string().min(1, 'API Key ID is required'),
  secret_key: z.string().min(1, 'Secret Key is required')
})

type CredentialsForm = z.infer<typeof credentialsSchema>

export function SetupPage() {
  const [status, setStatus] = useState<'idle' | 'checking' | 'success' | 'error'>('idle')
  const [statusMessage, setStatusMessage] = useState('')
  const [hasCredentials, setHasCredentials] = useState(false)
  
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting }
  } = useForm<CredentialsForm>({
    resolver: zodResolver(credentialsSchema)
  })
  
  const checkStatus = async () => {
    try {
      const response = await credentialsApi.getStatus()
      setHasCredentials(response.data.has_keys)
    } catch (error) {
      console.error('Failed to check credentials status:', error)
    }
  }
  
  const testConnection = async () => {
    setStatus('checking')
    setStatusMessage('Testing connection to Alpaca...')
    try {
      const response = await credentialsApi.test()
      const result: TestConnectionResult = response.data
      // The endpoint signals failure with HTTP 400 (handled below), so a 200
      // means the keys authenticated.
      setStatus('success')
      setStatusMessage(
        `Connected to Alpaca! Status: ${result.account_status ?? 'unknown'} | ` +
        `Equity: $${Number(result.equity).toLocaleString()} | ` +
        `Buying power: $${Number(result.buying_power).toLocaleString()}`
      )
    } catch (error: any) {
      setStatus('error')
      setStatusMessage(error.response?.data?.detail || 'Connection test failed')
    }
  }
  
  const onSubmit = async (data: CredentialsForm) => {
    setStatus('checking')
    setStatusMessage('Saving credentials...')
    try {
      await credentialsApi.save(data.key_id, data.secret_key)
      setStatus('success')
      setStatusMessage('Credentials saved successfully!')
      setHasCredentials(true)
      reset()
    } catch (error: any) {
      setStatus('error')
      setStatusMessage(error.response?.data?.detail || 'Failed to save credentials')
    }
  }
  
  React.useEffect(() => {
    checkStatus()
  }, [])
  
  return (
    <div className="max-w-2xl mx-auto space-y-4 sm:space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-body">Setup</h1>
        <p className="mt-1 text-sm text-muted">
          Configure your Alpaca Paper Trading API credentials to get started.
        </p>
      </div>
      
      {/* Credentials Status */}
      <div className="card">
        <h2 className="text-lg font-medium text-body mb-4">Connection Status</h2>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center">
            <div className={`h-3 w-3 rounded-full ${
              hasCredentials ? 'bg-success' : 'bg-line'
            }`} />
            <span className="ml-3 text-sm text-body">
              {hasCredentials ? 'Credentials configured' : 'No credentials configured'}
            </span>
          </div>
          {hasCredentials && (
            <button
              onClick={testConnection}
              disabled={status === 'checking'}
              className="btn-secondary text-sm"
            >
              <CheckCircle className="h-4 w-4 mr-2" />
              Test Connection
            </button>
          )}
        </div>
        
        {statusMessage && (
          <div className={`mt-4 p-3 rounded-md text-sm ${
            status === 'success' ? 'bg-success-soft text-success-fg' :
            status === 'error' ? 'bg-danger-soft text-danger-fg' :
            'bg-accent-soft text-accent'
          }`}>
            <div className="flex items-center">
              {status === 'success' && <CheckCircle className="h-4 w-4 mr-2" />}
              {status === 'error' && <AlertCircle className="h-4 w-4 mr-2" />}
              {status === 'checking' && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              <span>{statusMessage}</span>
            </div>
          </div>
        )}
      </div>
      
      {/* Credentials Form */}
      {!hasCredentials && (
        <div className="card">
          <h2 className="text-lg font-medium text-body mb-4">Enter API Credentials</h2>
          <p className="text-sm text-muted mb-6">
            Get your Paper Trading API keys from <a href="https://app.alpaca.markets/paper" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">Alpaca Dashboard</a>.
            Make sure to use <strong>Paper Trading</strong> keys, not Live Trading keys.
          </p>
          
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <div>
              <label htmlFor="key_id" className="label">API Key ID</label>
              <div className="relative">
                <Key className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-subtle" />
                <input
                  {...register('key_id')}
                  id="key_id"
                  type="text"
                  className="input pl-10"
                  placeholder="PKXXXXXXXXXXXXXXXXXXXX"
                  disabled={isSubmitting}
                />
              </div>
              {errors.key_id && (
                <p className="mt-1 text-sm text-danger-fg">{errors.key_id.message}</p>
              )}
            </div>
            
            <div>
              <label htmlFor="secret_key" className="label">Secret Key</label>
              <div className="relative">
                <Key className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-subtle" />
                <input
                  {...register('secret_key')}
                  id="secret_key"
                  type="password"
                  className="input pl-10"
                  placeholder="••••••••••••••••••••••••••••••••••••"
                  disabled={isSubmitting}
                />
              </div>
              {errors.secret_key && (
                <p className="mt-1 text-sm text-danger-fg">{errors.secret_key.message}</p>
              )}
            </div>
            
            <button
              type="submit"
              disabled={isSubmitting}
              className="btn-primary w-full"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Save className="h-4 w-4 mr-2" />
                  Save Credentials
                </>
              )}
            </button>
          </form>
        </div>
      )}
      
      {/* Info Box */}
      <div className="bg-accent-soft border border-line-accent rounded-lg p-4">
        <h3 className="text-sm font-medium text-accent mb-2">Security Notes</h3>
        <ul className="text-sm text-accent space-y-1">
          <li>• API keys are encrypted at rest using Fernet (AES-128-GCM)</li>
          <li>• Only Paper Trading endpoint is used — live trading is impossible</li>
          <li>• Keys are never logged or exposed in the frontend</li>
          <li>• Encryption key is set via SECRET_ENCRYPTION_KEY environment variable</li>
        </ul>
      </div>
    </div>
  )
}