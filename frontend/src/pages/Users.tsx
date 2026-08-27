import { useQuery } from '@tanstack/react-query'
import { Ban, CircleSlash, Clock, Search, UserCheck, Users as UsersIcon } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Button, Card, Input } from '@/components/ui'
import { panel, type PanelUser } from '@/lib/api'
import { bytes, dateTime, number, untilExpiry } from '@/lib/format'
import { UserEditor } from './UserEditor'

const PAGE_SIZE = 25

const STATUS_STYLE: Record<PanelUser['status'], string> = {
  ACTIVE: 'bg-success/15 text-success',
  EXPIRED: 'bg-danger/15 text-danger',
  LIMITED: 'bg-warning/15 text-warning',
  DISABLED: 'bg-muted/20 text-muted',
}

const STATUS_LABEL: Record<PanelUser['status'], string> = {
  ACTIVE: 'ACTIVE',
  EXPIRED: 'EXPIRED',
  LIMITED: 'LIMITED',
  DISABLED: 'DISABLED',
}

function CountCard({
  icon,
  label,
  value,
  tone,
}: {
  icon: ReactNode
  label: string
  value: number
  tone: string
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-3">
        <span className={`grid size-9 shrink-0 place-items-center rounded-2xl ${tone}`}>
          {icon}
        </span>
        <div className="min-w-0">
          <div className="truncate text-xs text-muted">{label}</div>
          <div className="text-xl font-semibold tabular-nums">{number(value)}</div>
        </div>
      </div>
    </Card>
  )
}

export default function Users() {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [editing, setEditing] = useState<PanelUser | null>(null)

  const { data: counts } = useQuery({
    queryKey: ['user-status-counts'],
    queryFn: panel.userStatusCounts,
    retry: false,
  })

  const { data, isPending, error } = useQuery({
    queryKey: ['users', query, page],
    queryFn: () =>
      panel.users({ start: page * PAGE_SIZE, size: PAGE_SIZE, search: query }),
  })

  const totalPages = Math.ceil((data?.total ?? 0) / PAGE_SIZE)

  return (
    <div className="space-y-4">
      {counts && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <CountCard
            icon={<UsersIcon className="size-4" />}
            label="Всего"
            value={counts.total}
            tone="bg-accent/15 text-accent"
          />
          <CountCard
            icon={<UserCheck className="size-4" />}
            label="Активные"
            value={counts.active}
            tone="bg-success/15 text-success"
          />
          <CountCard
            icon={<Clock className="size-4" />}
            label="Истёкшие"
            value={counts.expired}
            tone="bg-danger/15 text-danger"
          />
          <CountCard
            icon={<CircleSlash className="size-4" />}
            label="С лимитом"
            value={counts.limited}
            tone="bg-warning/15 text-warning"
          />
          <CountCard
            icon={<Ban className="size-4" />}
            label="Отключённые"
            value={counts.disabled}
            tone="bg-muted/20 text-muted"
          />
        </div>
      )}

      <Card className="p-0">
        <form
          className="flex gap-2 border-b border-border/60 p-4"
          onSubmit={(e) => {
            e.preventDefault()
            setPage(0)
            setQuery(search.trim())
          }}
        >
          <div className="relative max-w-md flex-1">
            <Search className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted" />
            <Input
              className="pl-10"
              placeholder="Имя, e-mail или Telegram ID"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Button type="submit">Найти</Button>
          {query && (
            <Button
              variant="ghost"
              type="button"
              onClick={() => {
                setSearch('')
                setQuery('')
                setPage(0)
              }}
            >
              Сбросить
            </Button>
          )}
        </form>

        {error && <p className="p-4 text-sm text-danger">{(error as Error).message}</p>}

        {isPending || !data ? (
          <p className="p-4 text-sm text-muted">Загрузка…</p>
        ) : data.users.length === 0 ? (
          <p className="p-4 text-sm text-muted">
            {query ? 'Никого не найдено.' : 'Пользователей пока нет.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/60 text-left text-xs text-muted">
                  <th className="px-4 py-3 font-medium">Пользователь</th>
                  <th className="px-4 py-3 font-medium">Статус</th>
                  <th className="px-4 py-3 font-medium">Истекает</th>
                  <th className="px-4 py-3 font-medium">Трафик</th>
                  <th className="px-4 py-3 font-medium">Тег</th>
                  <th className="px-4 py-3 font-medium">Был в сети</th>
                  <th className="px-4 py-3 text-right font-medium">Устройства</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {data.users.map((user) => {
                  const expiry = untilExpiry(user.expire_at)
                  return (
                    <tr
                      key={user.id}
                      onClick={() => setEditing(user)}
                      className="cursor-pointer transition-colors hover:bg-surface-hover"
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium">{user.username}</div>
                        <div className="text-xs text-muted">
                          {user.telegram_id ? `TG ${user.telegram_id}` : 'без Telegram'}
                          {user.email && ` · ${user.email}`}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2 py-1 text-xs font-medium ${STATUS_STYLE[user.status]}`}
                        >
                          {STATUS_LABEL[user.status]}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={expiry.expired ? 'text-danger' : ''}>
                          {expiry.text}
                        </span>
                      </td>
                      <td className="px-4 py-3 tabular-nums">
                        {bytes(user.used_traffic_bytes)}
                        {user.traffic_limit_bytes > 0 && (
                          <span className="text-muted">
                            {' '}
                            / {bytes(user.traffic_limit_bytes)}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {user.tag ? (
                          <span className="font-mono text-xs text-muted">{user.tag}</span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-muted">
                        {user.online_at ? dateTime(user.online_at) : 'никогда'}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted">
                        {user.hwid_device_limit ?? '∞'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {!query && totalPages > 1 && (
          <div className="flex items-center gap-3 border-t border-border/60 p-4 text-sm">
            <Button
              variant="ghost"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              Назад
            </Button>
            <span className="text-muted">
              Страница {page + 1} из {totalPages} · всего {number(data?.total ?? 0)}
            </span>
            <Button
              variant="ghost"
              disabled={page + 1 >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Вперёд
            </Button>
          </div>
        )}
      </Card>

      {editing && <UserEditor user={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}
