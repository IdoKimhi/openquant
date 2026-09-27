// Layout wrapper component
import { ReactNode } from 'react'
import { Outlet } from 'react-router-dom'

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="lg:pl-64 min-h-screen">
      <header className="sticky top-0 z-40 bg-white border-b border-gray-200">
        <div className="flex items-center justify-between h-16 px-4 sm:px-6 lg:px-8">
          <div className="flex items-center">
            <h2 className="text-lg font-semibold text-gray-900" id="page-title">
              {/* Page title will be set by individual pages */}
            </h2>
          </div>
          <div className="flex items-center space-x-4">
            {/* Connection status indicator could go here */}
          </div>
        </div>
      </header>
      
      <main className="p-4 sm:p-6 lg:p-8">
        <Outlet />
        {children}
      </main>
    </div>
  )
}