import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'openquant.theme'

interface ThemeContextValue {
  /** The theme actually in effect right now. */
  theme: Theme
  /** Explicitly choose light or dark, and stop following the OS. */
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  /** True until the user picks a theme, i.e. the OS preference is still driving. */
  followsSystem: boolean
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readStoredPreference(): Theme | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : null
  } catch {
    // Private browsing / disabled storage. The toggle still works for this
    // session; it just will not be remembered.
    return null
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Seed from the class the pre-paint script in index.html already put on
  // <html>, rather than re-deciding here. That script exists so the page does
  // not flash white before React mounts; if this hook reimplemented the same
  // logic it could disagree with it, and the first paint would lie about the
  // theme. Reading the class makes disagreement structurally impossible.
  const [preference, setPreference] = useState<Theme | null>(readStoredPreference)
  const [systemTheme, setSystemTheme] = useState<Theme>(() =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light',
  )

  // Keep tracking the OS for as long as the user has not chosen. Once they
  // have, this is inert and the stored preference wins for the rest of the
  // session and every later one.
  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (event: MediaQueryListEvent) => setSystemTheme(event.matches ? 'dark' : 'light')
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const theme: Theme = preference ?? systemTheme

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', theme === 'dark')
  }, [theme])

  const setTheme = useCallback((next: Theme) => {
    setPreference(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // See readStoredPreference - non-fatal.
    }
  }, [])

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : 'dark')
  }, [theme, setTheme])

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, toggleTheme, followsSystem: preference === null }),
    [theme, preference, setTheme, toggleTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme must be used inside a ThemeProvider')
  }
  return context
}
