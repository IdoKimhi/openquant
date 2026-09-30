// Setup Page - Enter, rotate and remove Alpaca API credentials
import React from 'react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Save, CheckCircle, AlertCircle, Loader2, Key, Trash2, ShieldCheck } from 'lucide-react'
import {
  credentialsApi,
  CredentialsInfo,
  TestConnectionResult
} from '../api/credentials'
import { safeFormat } from '../lib/format'

const credentialsSchema = z.object({
  key_id: z.string().min(1, 'API Key ID is required'),
  secret_key: z.string().min(1, 'Secret Key is required')
})

type CredentialsForm = z.infer<typeof credentialsSchema>

// A missing number renders as '-', not "$NaN". A currency formatter handed
// null produces "NaN" rather than throwing, so it fails quietly on screen.
const money = (value: number | null) =>
  value === null || value === undefined
    ? '-'
    : `$${value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
      })}`

export function SetupPage() {
  const [status, setStatus] = useState<'idle' | 'checking' | 'success' | 'error'>('idle')
  const [statusMessage, setStatusMessage] = useState('')
  const [hasCredentials, setHasCredentials] = useState(false)
  const [info, setInfo] = useState<CredentialsInfo | null>(null)
  // Two-step delete. Removing the keys un-configures the app and the worker
  // then goes quiet rather than loud: it logs "Failed to create Alpaca client
  // (no credentials)" and skips the cycle. That is deliberate, but it is not
  // something to do behind a single click - and the stored secret cannot be
  // read back out of this app, so a removal is a one-way door.
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const {
    register,
    handleSubmit,
    reset,
    getValues,
    formState: { errors, isSubmitting }
  } = useForm<CredentialsForm>({
    resolver: zodResolver(credentialsSchema)
  })

  const checkStatus = async () => {
    try {
      const response = await credentialsApi.getStatus()
      setHasCredentials(response.data.has_keys)
      if (response.data.has_keys) {
        // Masked detail for the panel. A 404 here only means unconfigured,
        // which getStatus has already reported, so it is not surfaced.
        try {
          const detail = await credentialsApi.getInfo()
          setInfo(detail.data)
        } catch {
          setInfo(null)
        }
      } else {
        setInfo(null)
      }
    } catch (error) {
      console.error('Failed to check credentials status:', error)
    }
  }

  const describeAccount = (result: TestConnectionResult) =>
    `Connected to Alpaca! Status: ${result.account_status ?? 'unknown'} | ` +
    `Equity: ${money(result.equity)} | ` +
    `Buying power: ${money(result.buying_power)}`

  const testConnection = async () => {
    setStatus('checking')
    setStatusMessage('Testing connection to Alpaca...')
    try {
      const response = await credentialsApi.test()
      // The endpoint signals failure with an HTTP error (handled below), so a
      // 200 means the stored keys authenticated.
      setStatus('success')
      setStatusMessage(describeAccount(response.data))
    } catch (error: any) {
      setStatus('error')
      setStatusMessage(error.response?.data?.detail || 'Connection test failed')
    }
  }

  // Validates the keys currently in the form *without* storing them, so a
  // rotation can be checked before it is committed. Reading the account back
  // is also how the operator confirms they pasted the new account's keys and
  // not the old ones again - the equity is the only thing that distinguishes
  // them.
  const testTypedKeys = async () => {
    const { key_id, secret_key } = getValues()
    if (!key_id || !secret_key) {
      setStatus('error')
      setStatusMessage('Enter both an API Key ID and a Secret Key to test them.')
      return
    }
    setStatus('checking')
    setStatusMessage('Testing these keys with Alpaca (nothing will be saved)...')
    try {
      const response = await credentialsApi.testProvided(key_id, secret_key)
      setStatus('success')
      setStatusMessage(
        describeAccount(response.data) + ' These keys work - save them to use this account.'
      )
    } catch (error: any) {
      setStatus('error')
      setStatusMessage(
        (error.response?.data?.detail || 'Those keys were rejected') +
          ' Your stored credentials are unchanged.'
      )
    }
  }

  const onSubmit = async (data: CredentialsForm) => {
    setStatus('checking')
    setStatusMessage('Validating and saving credentials...')
    try {
      // The backend validates against the broker before it overwrites, so a
      // rejected key comes back as an error with the working one still in
      // place. That is the whole point of validating here rather than after.
      await credentialsApi.save(data.key_id, data.secret_key)
      setStatus('success')
      setStatusMessage('Credentials saved successfully!')
      reset()
      setConfirmingDelete(false)
      await checkStatus()
    } catch (error: any) {
      setStatus('error')
      setStatusMessage(error.response?.data?.detail || 'Failed to save credentials')
    }
  }

  const removeCredentials = async () => {
    setStatus('checking')
    setStatusMessage('Removing stored credentials...')
    try {
      await credentialsApi.remove()
      setHasCredentials(false)
      setInfo(null)
      setConfirmingDelete(false)
      reset()
      setStatus('success')
      setStatusMessage(
        'Credentials removed. If the bot is running it will skip its cycles until new keys are saved.'
      )
    } catch (error: any) {
      setStatus('error')
      setStatusMessage(error.response?.data?.detail || 'Failed to remove credentials')
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
            <div
              className={`h-3 w-3 rounded-full shrink-0 ${
                hasCredentials ? 'bg-success' : 'bg-line'
              }`}
            />
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

        {/* Which account is this?

            The secret is not recoverable from this app, so the last four
            characters and the date stored are the only way to tell a freshly
            rotated key from the one it replaced - which is the question a
            rotation actually starts with. */}
        {hasCredentials && info && (
          <div className="mt-4 pt-4 border-t border-line text-sm">
            <dl className="grid grid-cols-2 gap-y-1">
              <dt className="text-muted">Key ID</dt>
              <dd className="text-body font-mono">••••••••••••{info.key_id_last4}</dd>
              <dt className="text-muted">Stored</dt>
              <dd className="text-body">
                {safeFormat(info.created_at, 'd MMM yyyy, HH:mm')}
              </dd>
            </dl>
          </div>
        )}

        {statusMessage && (
          <div
            className={`mt-4 p-3 rounded-md text-sm ${
              status === 'success'
                ? 'bg-success-soft text-success-fg'
                : status === 'error'
                  ? 'bg-danger-soft text-danger-fg'
                  : 'bg-accent-soft text-accent'
            }`}
          >
            <div className="flex items-start">
              {status === 'success' && (
                <CheckCircle className="h-4 w-4 mr-2 mt-0.5 shrink-0" />
              )}
              {status === 'error' && (
                <AlertCircle className="h-4 w-4 mr-2 mt-0.5 shrink-0" />
              )}
              {status === 'checking' && (
                <Loader2 className="h-4 w-4 mr-2 mt-0.5 animate-spin shrink-0" />
              )}
              <span>{statusMessage}</span>
            </div>
          </div>
        )}
      </div>

      {/* Credentials Form.

          Rendered whether or not keys are already stored. This block used to be
          behind `{!hasCredentials && ...}`, which meant that once a key was
          saved there was no way to replace it from the UI at all: the rotation
          half of issue #7 was reachable through the API and nowhere else. */}
      <div className="card">
        <h2 className="text-lg font-medium text-body mb-4">
          {hasCredentials ? 'Replace API Credentials' : 'Enter API Credentials'}
        </h2>
        <p className="text-sm text-muted mb-6">
          {hasCredentials ? (
            'Saving here replaces the stored key. Test the new keys first if you want to check them before they take effect.'
          ) : (
            <>
              Get your Paper Trading API keys from{' '}
              <a
                href="https://app.alpaca.markets/paper"
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent hover:underline"
              >
                Alpaca Dashboard
              </a>
              . Make sure to use <strong>Paper Trading</strong> keys, not Live
              Trading keys.
            </>
          )}
        </p>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <label htmlFor="key_id" className="label">
              API Key ID
            </label>
            <div className="relative">
              <Key className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-subtle" />
              <input
                {...register('key_id')}
                id="key_id"
                type="text"
                className="input pl-10"
                placeholder="PKXXXXXXXXXXXXXXXXXXXX"
                autoComplete="off"
                disabled={isSubmitting}
              />
            </div>
            {errors.key_id && (
              <p className="mt-1 text-sm text-danger-fg">{errors.key_id.message}</p>
            )}
          </div>

          <div>
            <label htmlFor="secret_key" className="label">
              Secret Key
            </label>
            <div className="relative">
              <Key className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-subtle" />
              <input
                {...register('secret_key')}
                id="secret_key"
                type="password"
                className="input pl-10"
                placeholder="••••••••••••••••••••••••••••••••••"
                autoComplete="off"
                disabled={isSubmitting}
              />
            </div>
            {errors.secret_key && (
              <p className="mt-1 text-sm text-danger-fg">{errors.secret_key.message}</p>
            )}
          </div>

          <div className="flex flex-col sm:flex-row gap-2">
            <button type="submit" disabled={isSubmitting} className="btn-primary flex-1">
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Save className="h-4 w-4 mr-2" />
                  {hasCredentials ? 'Replace Credentials' : 'Save Credentials'}
                </>
              )}
            </button>
            <button
              type="button"
              onClick={testTypedKeys}
              disabled={status === 'checking' || isSubmitting}
              className="btn-secondary"
            >
              <ShieldCheck className="h-4 w-4 mr-2" />
              Test Without Saving
            </button>
          </div>
        </form>
      </div>

      {/* Remove */}
      {hasCredentials && (
        <div className="card">
          <h2 className="text-lg font-medium text-body mb-2">Remove Credentials</h2>
          <p className="text-sm text-muted mb-4">
            Returns the app to its unconfigured state. The stored key cannot be
            read back out of this app, so make sure you have what you need
            before removing it.
          </p>
          {confirmingDelete ? (
            <div className="flex flex-col sm:flex-row gap-2">
              <button
                onClick={removeCredentials}
                disabled={status === 'checking'}
                className="btn-danger"
              >
                <Trash2 className="h-4 w-4 mr-2" />
                Yes, remove them
              </button>
              <button
                onClick={() => setConfirmingDelete(false)}
                disabled={status === 'checking'}
                className="btn-secondary"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button onClick={() => setConfirmingDelete(true)} className="btn-secondary">
              <Trash2 className="h-4 w-4 mr-2" />
              Remove Credentials
            </button>
          )}
        </div>
      )}

      {/* Info Box */}
      <div className="bg-accent-soft border border-line-accent rounded-lg p-4">
        <h3 className="text-sm font-medium text-accent mb-2">Security Notes</h3>
        <ul className="text-sm text-accent space-y-1">
          <li>• API keys are encrypted at rest using Fernet (AES-128-GCM)</li>
          <li>• Only Paper Trading endpoint is used — live trading is impossible</li>
          <li>
            • The secret key is never sent back to the browser — only the last four
            characters of the key ID are ever displayed
          </li>
          <li>• Encryption key is set via SECRET_ENCRYPTION_KEY environment variable</li>
        </ul>
      </div>
    </div>
  )
}
