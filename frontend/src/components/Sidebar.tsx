// Sidebar navigation component
import { useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Settings, Zap, Clock, TerminalSquare, Bot, LogOut, X } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Setup', href: '/setup', icon: Settings },
  { name: 'Strategy', href: '/strategy', icon: Zap },
  { name: 'Schedule', href: '/schedule', icon: Clock },
  { name: 'Control', href: '/control', icon: TerminalSquare },
  { name: 'Agents', href: '/agents', icon: Bot },
]

interface SidebarProps {
  /**
   * Whether the drawer is open on a small screen (issue #1).
   *
   * Owned by Layout rather than kept as state in here, because two things have
   * to react to it - the backdrop and the Escape key - and neither can see
   * state that lives inside the drawer it is supposed to be dismissing.
   */
  open: boolean
  onClose: () => void
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const { logout } = useAuth()

  // Escape closes the drawer. Without it a mobile user who opened the menu has
  // no way back to the page except a tap on the backdrop, which is a target
  // they have to be able to hit on a phone.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <>
      {/*
        Backdrop. Always mounted, but inert when closed - `pointer-events-none`
        alongside `opacity-0`, because a transparent div still eats clicks, and
        the whole page would be unclickable with no drawer on screen.

        `lg:hidden`, and it has to be: the sidebar's own visibility is a
        `translate-x` that lg cancels, and this element has no equivalent. A
        scrim left mounted on desktop would sit over the content forever, so
        the breakpoint has to be expressed here rather than inherited.
      */}
      <div
        onClick={onClose}
        aria-hidden="true"
        className={`fixed inset-0 z-40 bg-black/50 transition-opacity duration-300 ease-in-out lg:hidden ${
          open ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      />

      <aside
        // `overflow-y-auto` on the aside, not just on the nav: the nav scrolls,
        // but the logo and the sign-out button are outside it, and on a short
        // phone viewport the nav's own scroll area can be pushed to zero
        // height - leaving the user with a logo and no way to sign out.
        className={`fixed inset-y-0 left-0 z-50 w-64 bg-surface shadow-lg transform transition-transform duration-300 ease-in-out lg:translate-x-0 ${
          // Off-canvas on mobile, pinned on desktop. The `lg:` variant has to
          // win regardless of `open`, hence the same class in both branches
          // rather than relying on source order.
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
        aria-label="Main navigation"
      >
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center justify-between h-16 border-b border-line px-4">
            <h1 className="text-xl font-bold text-accent">OpenQuant</h1>
            {/* Only reachable on mobile; the permanent sidebar has no need. */}
            <button
              onClick={onClose}
              className="lg:hidden p-1 -mr-1 rounded-md text-muted hover:bg-surface-hover hover:text-body"
              aria-label="Close navigation"
            >
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>

          {/* Navigation. NavLink already reports its own active state, so the
              earlier mix of `location.pathname ===` for icons and the callback
              for the class is gone - they disagreed whenever a nested route
              matched.

              `onClick={onClose}` because on mobile the drawer covers the page:
              tapping a link navigates, but the drawer stays on top of the
              destination and the navigation appears to have done nothing. */}
          <nav className="flex-1 px-4 py-4 space-y-1 overflow-y-auto">
            {navigation.map((item) => (
              <NavLink
                key={item.name}
                to={item.href}
                onClick={onClose}
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
                      className={`h-5 w-5 mr-3 flex-shrink-0 ${isActive ? 'text-accent' : 'text-subtle'}`}
                      aria-hidden="true"
                    />
                    {item.name}
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          {/* Bottom: Logout */}
          <div className="p-4 border-t border-line flex-shrink-0">
            <button
              onClick={logout}
              className="flex items-center w-full px-3 py-2.5 text-sm font-medium text-muted rounded-md hover:bg-surface-hover hover:text-body transition-colors"
            >
              <LogOut className="h-5 w-5 mr-3 flex-shrink-0 text-subtle" aria-hidden="true" />
              Sign out
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}
