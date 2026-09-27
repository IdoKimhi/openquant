// API client configuration
import axios from 'axios'

export const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json'
  }
})

// Response interceptor for error handling
api.interceptors.response.use(
  response => response,
  error => {
    const url = error.config?.url ?? ''
    // A 401 from the login endpoint means "wrong password", not "session
    // expired". Redirecting there would reload the page and wipe the error
    // message the login form is about to render.
    const isLoginAttempt = url.includes('/auth/login')

    if (error.response?.status === 401 && !isLoginAttempt) {
      // Token expired or invalid
      localStorage.removeItem('token')
      delete api.defaults.headers.common['Authorization']
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)