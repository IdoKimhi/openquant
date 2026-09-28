// Light/dark switch, top right of the header.
import { Moon, Sun } from 'lucide-react'
import { useTheme } from '../hooks/useTheme'

export function ThemeToggle() {
  const { theme, toggleTheme, followsSystem } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'

  return (
    <button
      type="button"
      onClick={toggleTheme}
      // Naming the destination rather than the current state, so the label
      // stays truthful when it is read as an action.
      aria-label={`Switch to ${next} theme`}
      title={
        followsSystem
          ? `Following your system setting (${theme}). Click for ${next}.`
          : `Switch to ${next} theme`
      }
      className="inline-flex items-center justify-center h-9 w-9 rounded-md text-muted hover:text-body hover:bg-surface-hover transition-colors focus:outline-none focus:ring-2 focus:ring-accent-ring"
    >
      {theme === 'dark' ? (
        <Moon className="h-5 w-5" aria-hidden="true" />
      ) : (
        <Sun className="h-5 w-5" aria-hidden="true" />
      )}
    </button>
  )
}
