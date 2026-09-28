// Sidebar navigation component
import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Settings, Zap, Clock, TerminalSquare, Bot, LogOut } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Setup', href: '/setup', icon: Settings },
  { name: 'Strategy', href: '/strategy', icon: Zap },
  { name: 'Schedule', href: '/schedule', icon: Clock },
  { name: 'Control', href: '/control', icon: TerminalSquare },
  { name: 'Agents', href: '/agents', icon: Bot },
]

export function Sidebar() {
  const { logout } = useAuth()

  return (
    <aside className="fixed inset-y-0 left-0 z-50 w-64 bg-surface shadow-lg transform transition-transform duration-300 ease-in-out lg:translate-x-0">
      <div className="flex flex-col h-full">
        {/* Logo */}
        <div className="flex items-center justify-center h-16 border-b border-line px-4">
          <h1 className="text-xl font-bold text-accent">OpenQuant</h1>
        </div>

        {/* Navigation. NavLink already reports its own active state, so the
            earlier mix of `location.pathname ===` for icons and the callback
            for the class is gone - they disagreed whenever a nested route
            matched. */}
        <nav className="flex-1 px-4 py-4 space-y-1 overflow-y-auto">
          {navigation.map((item) => (
            <NavLink
              key={item.name}
              to={item.href}
              className={({ isActive }) =>
                `flex items-center px-3 py-2.5 text-sm font-medium rounded-md transition-colors ${
                  isActive
                    ? 'bg-accent-soft text-accent'
                    : 'text-muted hover:bg-surface-hover hover:text-body'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon
                    className={`h-5 w-5 mr-3 ${isActive ? 'text-accent' : 'text-subtle'}`}
                    aria-hidden="true"
                  />
                  {item.name}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Bottom: Logout */}
        <div className="p-4 border-t border-line">
          <button
            onClick={logout}
            className="flex items-center w-full px-3 py-2.5 text-sm font-medium text-muted rounded-md hover:bg-surface-hover hover:text-body transition-colors"
          >
            <LogOut className="h-5 w-5 mr-3 text-subtle" aria-hidden="true" />
            Sign out
          </button>
        </div>
      </div>
    </aside>
  )
}
