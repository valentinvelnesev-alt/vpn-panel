import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  BarChart3,
  Bot,
  CreditCard,
  LayoutDashboard,
  LogOut,
  Send,
  Server,
  Settings,
  Shield,
  Users,
} from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { auth } from '@/lib/api'
import { cn } from '@/lib/cn'
import { useNavStyle } from '@/lib/navStyle'

const NAV = [
  { to: '/', label: 'Обзор', icon: LayoutDashboard, end: true },
  { to: '/nodes', label: 'Ноды', icon: Server },
  { to: '/users', label: 'Пользователи', icon: Users },
  { to: '/bot', label: 'Бот', icon: Bot },
  { to: '/broadcasts', label: 'Рассылки', icon: Send },
  { to: '/payments', label: 'Платежи', icon: CreditCard },
  { to: '/analytics', label: 'Аналитика', icon: BarChart3 },
  { to: '/settings', label: 'Настройки', icon: Settings },
]

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={cn('flex items-center gap-2', compact ? '' : 'px-3 py-4')}>
      <span className="grid size-8 place-items-center rounded-2xl bg-accent/15 text-accent">
        <Shield className="size-4" />
      </span>
      <span className="text-sm font-semibold tracking-tight">VPN Panel</span>
    </div>
  )
}

function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: auth.logout,
    onSuccess: () => queryClient.setQueryData(['me'], null),
  })
}

const linkClass = (isActive: boolean, compact: boolean) =>
  cn(
    'flex items-center gap-2.5 rounded-full text-sm transition-all duration-200',
    compact ? 'px-3.5 py-2' : 'px-3.5 py-2.5',
    isActive
      ? 'bg-accent/15 font-medium text-accent shadow-sm shadow-accent/10'
      : 'text-muted hover:bg-surface-hover hover:text-fg',
  )

function SidebarLayout() {
  const logout = useLogout()

  return (
    <div className="flex min-h-dvh">
      <aside className="glass m-3 flex w-60 shrink-0 flex-col rounded-3xl p-3">
        <Brand />

        <nav className="mt-2 flex-1 space-y-1">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => linkClass(isActive, false)}
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>

        <button
          onClick={() => logout.mutate()}
          className="flex items-center gap-2.5 rounded-full px-3.5 py-2.5 text-sm text-muted transition-colors hover:bg-surface-hover hover:text-fg"
        >
          <LogOut className="size-4" />
          Выйти
        </button>
      </aside>

      <main className="flex-1 overflow-x-auto p-6">
        <Outlet />
      </main>
    </div>
  )
}

function CompactLayout() {
  const logout = useLogout()

  return (
    <div className="min-h-dvh">
      <header className="glass sticky top-3 z-20 mx-3 flex items-center gap-4 rounded-3xl px-4 py-2.5">
        <Brand compact />

        <nav className="flex flex-1 flex-wrap items-center gap-1">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => linkClass(isActive, true)}
            >
              <Icon className="size-4" />
              <span className="hidden lg:inline">{label}</span>
            </NavLink>
          ))}
        </nav>

        <button
          onClick={() => logout.mutate()}
          className="rounded-full p-2 text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          aria-label="Выйти"
        >
          <LogOut className="size-4" />
        </button>
      </header>

      <main className="p-6">
        <Outlet />
      </main>
    </div>
  )
}

export default function Layout() {
  return useNavStyle() === 'compact' ? <CompactLayout /> : <SidebarLayout />
}
