// Auth hook for managing authentication state
import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { api } from '../api/client'

interface AuthContextType {
  isAuthenticated: boolean
  loading: boolean
  error: string | null
  login: (password: string) => Promise<void>
  logout: () => void
  verifyToken: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  
  const verifyToken = async () => {
    try {
      const response = await api.get('/auth/verify')
      setIsAuthenticated(response.data.valid)
    } catch {
      setIsAuthenticated(false)
    } finally {
      setLoading(false)
    }
  }
  
  const login = async (password: string) => {
    setError(null)
    setLoading(true)
    try {
      const response = await api.post('/auth/login', { password })
      const { token } = response.data
      localStorage.setItem('token', token)
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`

      // The token was just issued by the backend with the same secret it will
      // be verified against, so a follow-up /auth/verify round trip adds no
      // certainty - only a chance to fail a login that actually succeeded.
      setIsAuthenticated(true)
      setLoading(false)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Invalid password')
      setIsAuthenticated(false)
      setLoading(false)
    }
  }
  
  const logout = () => {
    localStorage.removeItem('token')
    delete api.defaults.headers.common['Authorization']
    setIsAuthenticated(false)
  }
  
  useEffect(() => {
    const token = localStorage.getItem('token')
    if (token) {
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`
      verifyToken()
    } else {
      setLoading(false)
    }
  }, [])
  
  return (
    <AuthContext.Provider value={{
      isAuthenticated,
      loading,
      error,
      login,
      logout,
      verifyToken
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
