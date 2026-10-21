import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowDownToLine,
  BarChart3,
  BellRing,
  Bot,
  ChevronDown,
  Contact,
  CreditCard,
  DatabaseBackup,
  Gift,
  LayoutDashboard,
  LayoutList,
  Link2,
  LogOut,
  Menu,
  Palette,
  Plug,
  Send,
  Server,
  Settings,
  Shield,
  ShieldCheck,
  Tags,
  Ticket,
  Users,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useState, type MouseEvent } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { auth } from '@/lib/api'
import { useBotStatus } from '@/lib/botSettings'
import { useBrand } from '@/lib/brand'
import { cn } from '@/lib/cn'
import { usePanelTerms } from '@/lib/panelKind'
import { confirmLeave } from '@/lib/unsaved'

interface Leaf {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

interface Branch {
  label: string
  icon: LucideIcon
  base: string
  children: Leaf[]
  badge?: 'bot'
}

type Item = Leaf | Branch

interface Group {
  title: string
  items: Item[]
}

const isBranch = (item: Item): item is Branch => 'children' in item

function useNav(): Group[] {
  const terms = usePanelTerms()
  return [
    {
      title: 'Обзор',
      items: [
        { to: '/', label: 'Главная', icon: LayoutDashboard, end: true },
        { to: '/analytics', label: 'Аналитика', icon: BarChart3 },
      ],
    },
    {
      title: 'VPN',
      items: [
        { to: '/nodes', label: 'Ноды', icon: Server },
        { to: '/users', label: 'Пользователи', icon: Users },
      ],
    },
    {
      title: 'Telegram-бот',
      items: [
        {
          label: 'Бот',
          icon: Bot,
          base: '/bot',
          badge: 'bot',
          children: [
            { to: '/bot', label: 'Подключение', icon: Plug, end: true },
            { to: '/bot/menu', label: 'Меню и тексты', icon: LayoutList },
            { to: '/bot/plans', label: 'Тарифы', icon: Tags },
            { to: '/bot/trial', label: 'Пробный период', icon: Gift },
            { to: '/bot/bonuses', label: 'Промокоды и рефералы', icon: Ticket },
            { to: '/bot/notifications', label: 'Уведомления и админы', icon: BellRing },
          ],
        },
        { to: '/broadcasts', label: 'Рассылки', icon: Send },
      ],
    },
    {
      title: 'Продажи',
      items: [
        { to: '/clients', label: 'Клиенты бота', icon: Contact },
        { to: '/payments', label: 'Платежи', icon: CreditCard },
      ],
    },
    {
      title: 'Система',
      items: [
        {
          label: 'Настройки',
          icon: Settings,
          base: '/settings',
          children: [
            { to: '/settings', label: `Подключение к ${terms.name}`, icon: Link2, end: true },
            { to: '/settings/security', label: 'Безопасность', icon: ShieldCheck },
            { to: '/settings/appearance', label: 'Оформление', icon: Palette },
            { to: '/settings/backups', label: 'Резервные копии', icon: DatabaseBackup },
            { to: '/settings/import', label: 'Перенос из другого бота', icon: ArrowDownToLine },
          ],
        },
      ],
    },
  ]
}

/** Не даём молча уйти со страницы с несохранёнными правками. */
const guard = (event: MouseEvent) => {
  if (!confirmLeave()) event.preventDefault()
}

const itemClass = (active: boolean, nested = false) =>
  cn(
    'group flex w-full items-center gap-3 rounded-2xl border text-sm transition-all duration-200',
    nested ? 'px-3 py-2' : 'px-3 py-2.5',
    active
      ? 'border-accent/50 bg-accent/12 font-medium text-accent shadow-sm shadow-accent/10'
      : 'border-transparent text-muted hover:bg-surface-hover hover:text-fg',
  )

function BotDot() {
  const { data } = useBotStatus()
  if (!data?.configured) return null
  const starting = data.enabled && data.state === 'stopped'
  const colour = starting
    ? 'bg-warning animate-pulse'
    : data.state === 'running'
      ? 'bg-success'
      : data.state === 'error'
        ? 'bg-danger'
        : 'bg-muted'
  const title = starting
    ? 'Бот запускается'
    : data.state === 'running'
      ? 'Бот работает'
      : data.state === 'error'
        ? 'Ошибка бота'
        : 'Бот остановлен'
  return <span className={cn('size-2 rounded-full', colour)} title={title} aria-label={title} />
}

function BranchItem({ item }: { item: Branch }) {
  const { pathname } = useLocation()
  const inside = pathname === item.base || pathname.startsWith(`${item.base}/`)
  const [open, setOpen] = useState(inside)
  useEffect(() => {
    if (inside) setOpen(true)
  }, [inside])
  const Icon = item.icon

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className={itemClass(inside && !open)}
      >
        <Icon className={cn('size-4 shrink-0', inside && 'text-accent')} />
        <span className={cn('flex-1 text-left', inside && 'text-fg')}>{item.label}</span>
        {item.badge === 'bot' && <BotDot />}
        <ChevronDown
          className={cn('size-4 shrink-0 transition-transform', open ? 'rotate-180' : '')}
        />
      </button>
      <div
        className={cn(
          'grid transition-all duration-300',
          open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
        )}
      >
        <div className="overflow-hidden">
          <div className="mt-1 space-y-0.5 border-l border-border/60 pl-3 ml-5">
            {item.children.map(({ to, label, icon: ChildIcon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                onClick={guard}
                className={({ isActive }) => itemClass(isActive, true)}
              >
                <ChildIcon className="size-4 shrink-0" />
                <span className="truncate">{label}</span>
              </NavLink>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function Brand() {
  const { title, logo } = useBrand()
  return (
    <div className="flex min-w-0 items-center gap-2.5 px-2 py-3">
      {logo ? (
        <img src={logo} alt="" className="size-9 shrink-0 rounded-2xl object-cover" />
      ) : (
        <span className="grid size-9 shrink-0 place-items-center rounded-2xl bg-accent/15 text-accent">
          <Shield className="size-4" />
        </span>
      )}
      <span className="truncate text-base font-semibold tracking-tight">{title}</span>
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

function Navigation() {
  const groups = useNav()
  const logout = useLogout()

  return (
    <div className="flex h-full flex-col">
      <Brand />
      <nav className="mt-2 flex-1 space-y-4 overflow-y-auto pr-1">
        {groups.map((group, index) => (
          <div
            key={group.title}
            className={cn(index > 0 && 'border-t border-dashed border-border/70 pt-4')}
          >
            <p className="mb-1.5 flex items-center gap-2 px-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
              <span className="h-3 w-0.5 rounded-full bg-accent" aria-hidden />
              {group.title}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) =>
                isBranch(item) ? (
                  <BranchItem key={item.base} item={item} />
                ) : (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={guard}
                    className={({ isActive }) => itemClass(isActive)}
                  >
                    <item.icon className="size-4 shrink-0" />
                    {item.label}
                  </NavLink>
                ),
              )}
            </div>
          </div>
        ))}
      </nav>
      <button
        onClick={(event) => {
          guard(event)
          if (!event.defaultPrevented) logout.mutate()
        }}
        className="mt-3 flex items-center gap-3 rounded-2xl px-3 py-2.5 text-sm text-muted transition-colors hover:bg-surface-hover hover:text-fg"
      >
        <LogOut className="size-4" />
        Выйти
      </button>
    </div>
  )
}

export default function Layout() {
  const [drawer, setDrawer] = useState(false)
  const { pathname } = useLocation()
  const { title } = useBrand()

  useEffect(() => {
    setDrawer(false)
    window.scrollTo({ top: 0 })
  }, [pathname])

  return (
    <div className="min-h-dvh lg:flex">
      {/* Компьютер: меню всегда слева. */}
      <aside className="glass sticky top-3 m-3 hidden h-[calc(100dvh-1.5rem)] w-72 shrink-0 rounded-3xl p-3 lg:block">
        <Navigation />
      </aside>

      {/* Телефон: шапка с кнопкой меню и выезжающая панель. */}
      <header className="glass sticky top-0 z-30 flex items-center gap-3 px-4 py-3 lg:hidden">
        <button
          onClick={() => setDrawer(true)}
          className="rounded-full p-2 text-muted hover:bg-surface-hover hover:text-fg"
          aria-label="Открыть меню"
        >
          <Menu className="size-5" />
        </button>
        <span className="truncate font-semibold">{title}</span>
      </header>
      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={() => setDrawer(false)}
            aria-label="Закрыть меню"
          />
          <aside className="glass absolute inset-y-0 left-0 w-[19rem] max-w-[85vw] rounded-r-3xl p-3">
            <button
              onClick={() => setDrawer(false)}
              className="absolute right-3 top-4 rounded-full p-2 text-muted hover:bg-surface-hover"
              aria-label="Закрыть меню"
            >
              <X className="size-5" />
            </button>
            <Navigation />
          </aside>
        </div>
      )}

      {/* Снизу запас под всплывающую панель «Сохранить». */}
      <main className="min-w-0 flex-1 px-4 pb-28 pt-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  )
}
