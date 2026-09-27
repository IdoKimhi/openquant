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
      setHasCredentials(response.data.has_credentials)
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
      if (result.success) {
        setStatus('success')
        setStatusMessage(`Connected successfully! Account: ${result.account?.id} | Equity: $${Number(result.account?.equity).toLocaleString()}`)
      } else {
        setStatus('error')
        setStatusMessage(result.message)
      }
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
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Setup</h1>
        <p className="mt-1 text-sm text-gray-500">
          Configure your Alpaca Paper Trading API credentials to get started.
        </p>
      </div>
      
      {/* Credentials Status */}
      <div className="card">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Connection Status</h2>
        <div className="flex items-center justify-between">
          <div className="flex items-center">
            <div className={`h-3 w-3 rounded-full ${
              hasCredentials ? 'bg-green-500' : 'bg-gray-300'
            }`} />
            <span className="ml-3 text-sm text-gray-700">
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
            status === 'success' ? 'bg-green-50 text-green-800' :
            status === 'error' ? 'bg-red-50 text-red-800' :
            'bg-blue-50 text-blue-800'
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
          <h2 className="text-lg font-medium text-gray-900 mb-4">Enter API Credentials</h2>
          <p className="text-sm text-gray-500 mb-6">
            Get your Paper Trading API keys from <a href="https://app.alpaca.markets/paper" target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">Alpaca Dashboard</a>.
            Make sure to use <strong>Paper Trading</strong> keys, not Live Trading keys.
          </p>
          
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <div>
              <label htmlFor="key_id" className="label">API Key ID</label>
              <div className="relative">
                <Key className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-400" />
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
                <p className="mt-1 text-sm text-red-600">{errors.key_id.message}</p>
              )}
            </div>
            
            <div>
              <label htmlFor="secret_key" className="label">Secret Key</label>
              <div className="relative">
                <Key className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-400" />
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
                <p className="mt-1 text-sm text-red-600">{errors.secret_key.message}</p>
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
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-blue-800 mb-2">Security Notes</h3>
        <ul className="text-sm text-blue-700 space-y-1">
          <li>• API keys are encrypted at rest using Fernet (AES-128-GCM)</li>
          <li>• Only Paper Trading endpoint is used — live trading is impossible</li>
          <li>• Keys are never logged or exposed in the frontend</li>
          <li>• Encryption key is set via SECRET_ENCRYPTION_KEY environment variable</li>
        </ul>
      </div>
    </div>
  )
}