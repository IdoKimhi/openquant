// Auth API
import { api } from './client'

export const authApi = {
  login: (password: string) => api.post('/auth/login', { password }),
  verify: () => api.get('/auth/verify')
}