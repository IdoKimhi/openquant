// Layout wrapper component
import { Outlet } from 'react-router-dom'
import { ThemeToggle } from './ThemeToggle'

export function Layout() {
  return (
    <div className="lg:pl-64 min-h-screen">
      <header className="sticky top-0 z-40 bg-surface border-b border-line">
        <div className="flex items-center justify-between h-16 px-4 sm:px-6 lg:px-8">
          {/* The page's own <h1> lives in <main>; this slot stays empty rather
              than duplicating it. */}
          <div className="flex items-center" />
          <div className="flex items-center gap-2">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="p-4 sm:p-6 lg:p-8">
        {/* Only <Outlet/>. This used to render {children} as well, and since
            App passed <Sidebar/> in as children, the sidebar was mounted twice
            - once correctly as a fixed overlay, and once again inside the
            page content flow. The fixed copy hid the duplicate on desktop and
            it double-drew on mobile. The sidebar now renders once, from
            SidebarHost below. */}
        <Outlet />
      </main>
    </div>
  )
}
