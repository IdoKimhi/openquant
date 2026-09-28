// Main App component with routing
import React from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { Layout } from './components/Layout'
import { SetupPage } from './pages/SetupPage'
import { StrategyPage } from './pages/StrategyPage'
import { SchedulePage } from './pages/SchedulePage'
import { ControlPage } from './pages/ControlPage'
import { DashboardPage } from './pages/DashboardPage'
import { AgentsPage } from './pages/AgentsPage'
import { useAuth, AuthProvider } from './hooks/useAuth'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-accent"></div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              {/* Sidebar and Layout are siblings. They used to be nested, with
                  the sidebar passed into Layout as children - which is why
                  Layout had to render both <Outlet/> and {children}, and the
                  sidebar ended up mounted twice. */}
              <div className="min-h-screen bg-canvas">
                <Sidebar />
                <Layout />
              </div>
            </ProtectedRoute>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="setup" element={<SetupPage />} />
          <Route path="strategy" element={<StrategyPage />} />
          <Route path="schedule" element={<SchedulePage />} />
          <Route path="control" element={<ControlPage />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="agents" element={<AgentsPage />} />
        </Route>
      </Routes>
    </AuthProvider>
  )
}

function LoginPage() {
  const { login, loading, error, isAuthenticated } = useAuth()
  const [password, setPassword] = React.useState('')

  // Once a login succeeds the /login route still renders (it is intentionally
  // outside ProtectedRoute), so send the user on to the dashboard.
  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    await login(password)
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas px-4">
      <div className="max-w-md w-full space-y-8">
        <div>
          <h2 className="mt-6 text-center text-3xl font-extrabold text-body">
            OpenQuant
          </h2>
          <p className="mt-2 text-center text-sm text-muted">
            Sign in to access your trading dashboard
          </p>
        </div>
        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
          <div>
            <label htmlFor="password" className="sr-only">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input"
              placeholder="Password"
            />
          </div>

          {error && (
            <div className="text-danger text-sm text-center" role="alert">
              {error}
            </div>
          )}

          <div>
            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full"
            >
              {loading ? 'Signing in...' : 'Sign in'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default App
