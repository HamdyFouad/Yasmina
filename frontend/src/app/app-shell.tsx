import * as React from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import {
  FileText,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  ReceiptText,
  ShieldCheck,
  Tags,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { NavLink, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

import { BrandWordmark } from '@/components/brand'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Separator } from '@/components/ui/separator'
import { useAuth } from '@/features/auth/auth-context'
import { API_BASE_URL } from '@/lib/env'
import { cn, titleCase } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end: boolean
  adminOnly?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/tenders', label: 'Tenders', icon: FileText, end: false },
  { to: '/offers', label: 'Offers', icon: Tags, end: false },
  { to: '/invoices', label: 'Invoices', icon: ReceiptText, end: false },
  {
    to: '/admin/catalog',
    label: 'Catalog Admin',
    icon: ShieldCheck,
    end: false,
    adminOnly: true,
  },
]

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { user, roles, hasRole } = useAuth()

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center px-5">
        <BrandWordmark />
      </div>
      <Separator />
      <nav className="flex-1 space-y-1 px-3 py-4" aria-label="Primary">
        {NAV_ITEMS.filter((item) => !item.adminOnly || hasRole('admin')).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                isActive && 'bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary',
              )
            }
          >
            <item.icon className="h-4 w-4" aria-hidden="true" />
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="space-y-3 px-5 pb-5">
        <Separator />
        <div className="space-y-1 text-[11px] text-muted-foreground">
          <div className="font-medium text-foreground">Signed in as</div>
          <div className="truncate">{user?.subject ?? '—'}</div>
          <div className="flex flex-wrap gap-1 pt-1">
            {roles.map((role) => (
              <Badge key={role} variant="secondary" className="px-1.5 py-0 text-[10px]">
                {titleCase(role)}
              </Badge>
            ))}
          </div>
        </div>
        <div className="rounded-md bg-muted px-2.5 py-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
          API
          <br />
          <span className="break-all">{API_BASE_URL}</span>
        </div>
      </div>
    </div>
  )
}

function Header({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [loggingOut, setLoggingOut] = React.useState(false)

  const initials = (user?.subject ?? '?')
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('')

  const handleLogout = async (): Promise<void> => {
    setLoggingOut(true)
    try {
      await logout()
      toast.success('Signed out')
      navigate('/login', { replace: true })
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <header className="flex h-16 items-center justify-between gap-3 border-b border-border bg-card/80 px-4 backdrop-blur sm:px-6">
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="lg:hidden"
          onClick={onOpenMobileNav}
          aria-label="Open navigation"
        >
          <Menu />
        </Button>
        <div className="hidden text-sm font-medium text-muted-foreground sm:block">
          Operations workspace
        </div>
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="h-10 gap-2 px-2">
            <Avatar className="h-8 w-8">
              <AvatarFallback>{initials || 'Y'}</AvatarFallback>
            </Avatar>
            <span className="hidden max-w-40 truncate text-sm font-medium sm:inline">
              {user?.subject ?? 'Account'}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel>Signed in</DropdownMenuLabel>
          <div className="px-2 pb-2 text-xs text-muted-foreground">
            <div className="truncate font-medium text-foreground">{user?.subject}</div>
            <div className="truncate">{user?.roles.map(titleCase).join(' · ')}</div>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => {
              void handleLogout()
            }}
            disabled={loggingOut}
            className="text-destructive focus:text-destructive"
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  )
}

export function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </div>
    )
  }

  if (status === 'unauthenticated') {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    )
  }

  return <Outlet />
}

export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { status, hasRole } = useAuth()

  if (status === 'loading') {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </div>
    )
  }

  if (!hasRole('admin')) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = React.useState(false)

  React.useEffect(() => {
    if (!mobileOpen) {
      return
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        setMobileOpen(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [mobileOpen])

  return (
    <div className="flex min-h-screen bg-background">
      <aside className="hidden w-64 shrink-0 border-r border-border bg-card lg:block">
        <SidebarContent />
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-slate-950/50"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="relative flex h-full w-72 flex-col border-r border-border bg-card shadow-xl">
            <div className="absolute right-3 top-4">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setMobileOpen(false)}
                aria-label="Close navigation"
              >
                <X />
              </Button>
            </div>
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <Header onOpenMobileNav={() => setMobileOpen(true)} />
        <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-6xl space-y-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
