// Layout wrapper component
import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Menu } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { ThemeToggle } from './ThemeToggle'

export function Layout() {
  // The mobile drawer's open state. Issue #1: below the lg breakpoint the
  // sidebar is off-canvas, so without a control to bring it back the six
  // navigation links are simply unreachable on a phone.
  //
  // Closed on every route change would be wrong in the other direction - the
  // drawer would snap shut mid-navigation - so it is dismissed by tapping a
  // link (see Sidebar) or the backdrop, not by a route watcher.
  const [navOpen, setNavOpen] = useState(false)

  return (
    <div className="lg:pl-64 min-h-screen">
      <header className="sticky top-0 z-40 bg-surface border-b border-line">
        <div className="flex items-center justify-between gap-3 h-16 px-4 sm:px-6 lg:px-8">
          {/* The page's own <h1> lives in <main>; this slot holds the mobile
              menu button rather than duplicating the title. It is
              `lg:hidden` because from lg up the sidebar is always visible and
              a button there would open a drawer over the page. */}
          <button
            onClick={() => setNavOpen(true)}
            className="lg:hidden -ml-1 p-2 rounded-md text-muted hover:bg-surface-hover hover:text-body"
            aria-label="Open navigation"
            aria-expanded={navOpen}
          >
            <Menu className="h-5 w-5" aria-hidden="true" />
          </button>
          {/* Keeps the theme toggle right-aligned once the button is gone. */}
          <div className="flex-1 lg:hidden" />

          <div className="flex items-center gap-2">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="p-4 sm:p-6 lg:p-8">
        {/* Only <Outlet/>. This used to render {children} too, and since App
            passed <Sidebar/> in as children, the sidebar was mounted twice -
            once correctly as a fixed overlay, and once again inside the page
            content flow. The fixed copy hid the duplicate on desktop and it
            double-drew on mobile. See the <Sidebar/> below for where it lives
            now and why the open state has to be a sibling of this <main>
            rather than trapped inside it. */}
        <Outlet />
      </main>

      {/* Rendered here, once, and nowhere else. It used to be mounted twice:
          once as a sibling of Layout from App, and again from {children}
          inside <main>. Two copies of the nav means two copies of the open
          flag - the header button opened one and the backdrop dismissed the
          other - so the two have to be one component with one owner.

          A sibling of <main> and not inside it, because the backdrop it
          renders is `fixed inset-0`: nested inside <main> it would be clipped
          by any ancestor that establishes a containing block, which is a bug
          that only appears once something adds `transform` or `filter` up
          here. */}
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
    </div>
  )
}
