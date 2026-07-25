import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Server,
  TrendingUp,
  Users,
  Wallet,
  Wifi,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { AreaChart } from '@/components/AreaChart'
import { Card } from '@/components/ui'
import { panel, type NodeLoad, type Overview } from '@/lib/api'
import { countryFlag } from '@/lib/flag'
import { bytes, number } from '@/lib/format'

function Stat({
  icon,
  label,
  value,
  hint,
  accent = false,
}: {
  icon: ReactNode
  label: string
  value: string
  hint?: string
  accent?: boolean
}) {
  return (
    <Card className="relative overflow-hidden">
      {/* Мягкий блик в углу — карточка перестаёт быть плоским прямоугольником. */}
      {accent && (
        <div className="pointer-events-none absolute -right-8 -top-8 size-28 rounded-full bg-accent/15 blur-2xl" />
      )}
      <div className="relative flex items-center gap-2 text-muted">
        <span
          className={`grid size-8 place-items-center rounded-2xl ${
            accent ? 'bg-accent/15 text-accent' : 'bg-surface-hover'
          }`}
        >
          {icon}
        </span>
        <span className="text-sm">{label}</span>
      </div>
      <div className="relative mt-3 text-3xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="relative mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  )
}

function StatusBar({ data }: { data: Overview }) {
  const segments = [
    { label: 'Активные', value: data.users_active, color: 'bg-success' },
    { label: 'Истёкшие', value: data.users_expired, color: 'bg-muted' },
    { label: 'С лимитом', value: data.users_limited, color: 'bg-warning' },
    { label: 'Отключённые', value: data.users_disabled, color: 'bg-danger' },
  ].filter((s) => s.value > 0)

  const total = segments.reduce((sum, s) => sum + s.value, 0)

  return (
    <Card>
      <h2 className="text-sm font-medium">Статусы подписок</h2>

      {total > 0 ? (
        <>
          {/* Одна полоса вместо четырёх цифр: доли видно сразу. */}
          <div className="mt-4 flex h-2.5 gap-1 overflow-hidden rounded-full">
            {segments.map((s) => (
              <div
                key={s.label}
                className={`${s.color} rounded-full transition-all`}
                style={{ width: `${(s.value / total) * 100}%` }}
                title={`${s.label}: ${s.value}`}
              />
            ))}
          </div>

          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {segments.map((s) => (
              <div key={s.label}>
                <dt className="flex items-center gap-1.5 text-xs text-muted">
                  <span className={`size-2 rounded-full ${s.color}`} />
                  {s.label}
                </dt>
                <dd className="mt-0.5 text-xl font-semibold tabular-nums">
                  {number(s.value)}
                </dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <p className="mt-3 text-sm text-muted">Пользователей пока нет.</p>
      )}
    </Card>
  )
}

function NodesLoad({ nodes }: { nodes: NodeLoad[] }) {
  const max = Math.max(1, ...nodes.map((n) => n.users_online))

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Нагрузка на ноды</h2>
        <Link to="/nodes" className="text-xs text-accent hover:underline">
          Все ноды
        </Link>
      </div>

      {nodes.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Нод пока нет.</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {nodes.map((node) => (
            <li key={node.name}>
              <div className="flex items-center justify-between text-sm">
                <span className="flex min-w-0 items-center gap-2">
                  {countryFlag(node.country_code) && (
                    <span className="leading-none">{countryFlag(node.country_code)}</span>
                  )}
                  <span className="truncate">{node.name}</span>
                  {!node.online && (
                    <span className="shrink-0 text-xs text-danger">недоступна</span>
                  )}
                </span>
                <span className="tabular-nums text-muted">{node.users_online}</span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-bg">
                <div
                  className={`h-full rounded-full transition-all ${
                    node.online ? 'bg-accent' : 'bg-danger/50'
                  }`}
                  style={{ width: `${(node.users_online / max) * 100}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export default function Dashboard() {
  const { data, isPending } = useQuery({
    queryKey: ['overview'],
    queryFn: panel.overview,
    refetchInterval: 30_000,
  })

  if (isPending) return <p className="text-sm text-muted">Загрузка…</p>
  if (!data) return null

  return (
    <div className="space-y-4">
      {!data.configured && (
        <Card>
          <h2 className="font-medium">Подключите Remnawave</h2>
          <p className="mt-1 text-sm text-muted">
            Панель пока не знает, откуда брать данные о нодах и пользователях.
            Укажите адрес и токен вашей Remnawave.
          </p>
          <Link
            to="/settings"
            className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline"
          >
            Перейти в настройки <ArrowRight className="size-4" />
          </Link>
        </Card>
      )}

      {data.error && (
        <Card className="border-warning/40">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
            <div>
              <p className="text-sm font-medium">Remnawave не отвечает</p>
              <p className="mt-0.5 text-sm text-muted">{data.error}</p>
            </div>
          </div>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          accent
          icon={<Wallet className="size-4" />}
          label="Выручка за всё время"
          value={`${number(Math.round(data.revenue_total_rub))} ₽`}
          hint={
            data.revenue_today_rub > 0
              ? `сегодня ${number(Math.round(data.revenue_today_rub))} ₽`
              : 'сегодня пока без оплат'
          }
        />
        <Stat
          icon={<Users className="size-4" />}
          label="Пользователей"
          value={number(data.users_total)}
          hint={`${number(data.users_active)} с активной подпиской`}
        />
        <Stat
          icon={<Wifi className="size-4" />}
          label="Онлайн сейчас"
          value={number(data.online_now)}
          hint={`${number(data.online_last_day)} за сутки`}
        />
        <Stat
          icon={<Server className="size-4" />}
          label="Ноды"
          value={`${data.nodes_online} / ${data.nodes_total}`}
          hint={
            data.nodes_online < data.nodes_total ? 'есть недоступные' : 'все на связи'
          }
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="flex items-center gap-2">
            <TrendingUp className="size-4 text-accent" />
            <h2 className="text-sm font-medium">Выручка за 14 дней</h2>
          </div>
          <div className="mt-5">
            <AreaChart data={data.revenue_daily} formatValue={(v) => `${number(v)} ₽`} />
          </div>
        </Card>

        <Card>
          <div className="flex items-center gap-2">
            <Users className="size-4 text-accent" />
            <h2 className="text-sm font-medium">Новые пользователи за 14 дней</h2>
          </div>
          <div className="mt-5">
            <AreaChart data={data.new_users_daily} formatValue={(v) => number(v)} />
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <StatusBar data={data} />
        <NodesLoad nodes={data.nodes_load} />
      </div>

      <Card>
        <div className="flex items-center gap-2 text-muted">
          <Activity className="size-4" />
          <span className="text-sm">Трафик через все ноды за всё время</span>
          <span className="ml-auto text-lg font-semibold tabular-nums text-fg">
            {bytes(data.traffic_lifetime_bytes)}
          </span>
        </div>
      </Card>
    </div>
  )
}
