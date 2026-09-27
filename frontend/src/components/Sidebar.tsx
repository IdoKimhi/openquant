// Sidebar navigation component
import { NavLink, useLocation } from 'react-router-dom'
import { LayoutDashboard, Settings, Zap, Clock, TerminalSquare, LogOut } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Setup', href: '/setup', icon: Settings },
  { name: 'Strategy', href: '/strategy', icon: Zap },
  { name: 'Schedule', href: '/schedule', icon: Clock },
  { name: 'Control', href: '/control', icon: TerminalSquare },
]

export function Sidebar() {
  const location = useLocation()
  const { logout } = useAuth()
  
  return (
    <aside className="fixed inset-y-0 left-0 z-50 w-64 bg-white shadow-lg transform transition-transform duration-300 ease-in-out lg:translate-x-0">
      <div className="flex flex-col h-full">
        {/* Logo */}
        <div className="flex items-center justify-center h-16 border-b border-gray-200 px-4">
          <h1 className="text-xl font-bold text-blue-600">Alpaca Bot</h1>
        </div>
        
        {/* Navigation */}
        <nav className="flex-1 px-4 py-4 space-y-1 overflow-y-auto">
          {navigation.map((item) => {
            const isActive = location.pathname === item.href
            return (
              <NavLink
                key={item.name}
                to={item.href}
                className={({ isActive }) =>
                  `flex items-center px-3 py-2.5 text-sm font-medium rounded-md transition-colors ${
                    isActive
                      ? 'bg-blue-50 text-blue-700'
                      : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                  }`
                }
              >
                <item.icon className={`h-5 w-5 mr-3 ${isActive ? 'text-blue-600' : 'text-gray-400'}`} aria-hidden="true" />
                {item.name}
              </NavLink>
            )
          })}
        </nav>
        
        {/* Bottom: Logout */}
        <div className="p-4 border-t border-gray-200">
          <button
            onClick={logout}
            className="flex items-center w-full px-3 py-2.5 text-sm font-medium text-gray-600 rounded-md hover:bg-gray-50 hover:text-gray-900 transition-colors"
          >
            <LogOut className="h-5 w-5 mr-3 text-gray-400" aria-hidden="true" />
            Sign out
          </button>
        </div>
      </div>
    </aside>
  )
}