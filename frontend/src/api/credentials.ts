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
// `status: "connected"` on success and raises HTTP 400 with a `detail` message
// on failure - it never returns a `success` flag.
export interface TestConnectionResult {
  status: string
  equity: string | null
  buying_power: string | null
  account_status: string | null
}

export const credentialsApi = {
  getStatus: () => api.get<CredentialsStatus>('/credentials/status'),
  save: (key_id: string, secret_key: string) => api.post('/credentials', { key_id, secret_key }),
  test: () => api.post<TestConnectionResult>('/credentials/test')
}