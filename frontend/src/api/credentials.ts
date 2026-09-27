// Credentials API
import { api } from './client'

export interface CredentialsStatus {
  has_credentials: boolean
}

export interface TestConnectionResult {
  success: boolean
  message: string
  account?: {
    id: string
    status: string
    currency: string
    equity: string
  }
}

export const credentialsApi = {
  getStatus: () => api.get<CredentialsStatus>('/credentials/status'),
  save: (key_id: string, secret_key: string) => api.post('/credentials', { key_id, secret_key }),
  test: () => api.post<TestConnectionResult>('/credentials/test')
}