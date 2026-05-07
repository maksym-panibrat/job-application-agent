import { ReactNode, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { IconButton } from './ui/IconButton'
import { ActionSheet, ActionSheetItem } from './ui/ActionSheet'
import { Settings, Coach, Hamburger, Sync } from './ui/icons'
import { useSyncControl } from '../lib/useSyncControl'

export interface AppShellProps {
  children: ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const { signOut, user } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const sync = useSyncControl({ enabled: !!user })
  const syncBusy = sync.isLive || sync.isPending
  const syncLabel = sync.isLive ? sync.label : 'Sync now'

  function openCoach() {
    const next = new URLSearchParams(location.search)
    next.set('coach', '1')
    navigate({ pathname: location.pathname, search: `?${next.toString()}` })
  }

  return (
    <div className="min-h-screen bg-bg text-text">
      <header className="sticky top-0 z-30 bg-surface border-b border-border">
        <div className="max-w-3xl mx-auto px-4 h-14 flex items-center justify-between gap-2">
          <Link to="/" className="font-bold text-text text-sm tracking-tight">Job Agent</Link>

          <nav className="flex items-center gap-1">
            {user && (
              <>
                <div className="hidden md:flex items-center gap-1">
                  <IconButton
                    aria-label={syncLabel}
                    title={syncLabel}
                    disabled={syncBusy}
                    onClick={() => sync.trigger('header_button')}
                  >
                    <Sync className={`w-5 h-5 ${syncBusy ? 'animate-spin' : ''}`} />
                  </IconButton>
                  {sync.isLive && (
                    <span
                      data-testid="header-sync-live-label"
                      className="text-xs text-muted ml-1 mr-2 max-w-[200px] truncate"
                      title={sync.label}
                    >
                      {sync.label}
                    </span>
                  )}
                  <Link
                    to="/settings"
                    aria-label="Settings"
                    className="inline-flex items-center justify-center w-11 h-11 rounded-md-token text-muted hover:bg-surface-2 hover:text-text"
                  >
                    <Settings className="w-5 h-5" />
                  </Link>
                  <IconButton aria-label="Coach" onClick={openCoach}>
                    <Coach className="w-5 h-5" />
                  </IconButton>
                  <button
                    type="button"
                    onClick={() => signOut()}
                    className="px-3 py-1.5 text-sm text-muted hover:text-text"
                  >
                    Sign out
                  </button>
                </div>

                <IconButton
                  aria-label="Open menu"
                  className="md:hidden"
                  onClick={() => setMenuOpen(true)}
                >
                  <Hamburger className="w-5 h-5" />
                </IconButton>
              </>
            )}
          </nav>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-6">{children}</main>

      <ActionSheet
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        title="Menu"
        heading="Menu"
      >
        <ActionSheetItem
          disabled={syncBusy}
          onClick={() => { setMenuOpen(false); sync.trigger('mobile_menu') }}
        >
          {syncLabel}
        </ActionSheetItem>
        <ActionSheetItem
          onClick={() => { setMenuOpen(false); navigate('/settings') }}
        >
          Settings
        </ActionSheetItem>
        <ActionSheetItem
          onClick={() => { setMenuOpen(false); openCoach() }}
        >
          Coach
        </ActionSheetItem>
        <ActionSheetItem intent="danger" onClick={() => { setMenuOpen(false); signOut() }}>
          Sign out
        </ActionSheetItem>
      </ActionSheet>
    </div>
  )
}
