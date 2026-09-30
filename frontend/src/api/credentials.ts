// Credentials API
import { api } from './client'

// Mirrors the backend CredentialsStatus. The field is `has_keys`, not
// `has_credentials` - the UI showed "No credentials configured" forever
// because it read a field the API never sends.
export interface CredentialsStatus {
  has_keys: boolean
  last_tested: string | null
}

// Mirrors the backend TestConnectionResponse. The endpoint returns 200 with
// `status: "connected"` on success and raises an HTTP error with a `detail`
// message on failure - it never returns a `success` flag.
//
// `equity` and `buying_power` are numbers, and were declared `str` on the
// backend while the SDK returns a `Decimal` - which the frontend papered over
// with `Number(...)`. Both ends are numbers now, so the `Number()` wrappers are
// gone; a formatter that receives a string here is how a `$NaN` reaches the
// screen (gotcha 3).
export interface TestConnectionResult {
  status: string
  equity: number | null
  buying_power: number | null
  account_status: string | null
}

// Mirrors the backend CredentialsInfo. Four characters of the key id and a
// timestamp - the secret is not recoverable from this app at all, so this is
// the only way to tell which account is configured.
export interface CredentialsInfo {
  key_id_last4: string
  created_at: string
}

export const credentialsApi = {
  getStatus: () => api.get<CredentialsStatus>('/credentials/status'),
  // 404 when nothing is stored, so callers should treat that as "unset" rather
  // than as an error worth shouting about on a setup screen.
  getInfo: () => api.get<CredentialsInfo>('/credentials'),
  save: (key_id: string, secret_key: string) => api.post('/credentials', { key_id, secret_key }),
  remove: () => api.delete('/credentials'),
  test: () => api.post<TestConnectionResult>('/credentials/test'),
  // Validates without storing. This is the non-destructive half of a rotation:
  // paste the new paper account's keys, read the equity back, and only then
  // commit them with `save`.
  testProvided: (key_id: string, secret_key: string) =>
    api.post<TestConnectionResult>('/credentials/test-provided', { key_id, secret_key })
}
