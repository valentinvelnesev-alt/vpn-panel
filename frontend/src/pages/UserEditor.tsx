import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CalendarPlus,
  Copy,
  Gauge,
  Mail,
  Shield,
  Smartphone,
  Trash2,
  User as UserIcon,
} from 'lucide-react'
import { useState } from 'react'
import { Modal } from '@/components/Modal'
import { Button, Field, Input } from '@/components/ui'
import { panel, type PanelUser, type UserUpdate } from '@/lib/api'
import { bytes, dateTime, untilExpiry } from '@/lib/format'

const GIB = 1024 ** 3

const STRATEGIES: { value: string; label: string }[] = [
  { value: 'NO_RESET', label: 'Никогда не сбрасывать' },
  { value: 'DAY', label: 'Ежедневно' },
  { value: 'WEEK', label: 'Еженедельно' },
  { value: 'MONTH', label: 'Ежемесячно' },
  { value: 'MONTH_ROLLING', label: 'Раз в 30 дней' },
]

/** Дата для <input type="datetime-local"> — он не понимает ISO с зоной. */
function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border border-border/60 p-4">
      <div className="flex items-center gap-2 border-b border-border/60 pb-3">
        <span className="grid size-7 place-items-center rounded-xl bg-accent/15 text-accent">
          {icon}
        </span>
        <h3 className="text-sm font-medium">{title}</h3>
      </div>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  )
}

function DevicesSection({ uuid }: { uuid: string }) {
  const queryClient = useQueryClient()
  const { data: devices, isPending } = useQuery({
    queryKey: ['devices', uuid],
    queryFn: () => panel.devices(uuid),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['devices', uuid] })
  const remove = useMutation({
    mutationFn: (hwid: string) => panel.deleteDevice(uuid, hwid),
    onSuccess: refresh,
  })
  const reset = useMutation({ mutationFn: () => panel.resetDevices(uuid), onSuccess: refresh })

  return (
    <Section icon={<Smartphone className="size-4" />} title="Подключённые устройства">
      {isPending ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : devices?.length ? (
        <>
          <ul className="divide-y divide-border/60">
            {devices.map((device) => (
              <li key={device.hwid} className="flex items-center justify-between gap-4 py-2">
                <div className="min-w-0 text-sm">
                  <p className="truncate">
                    {device.device_model || device.platform || 'Неизвестное устройство'}
                  </p>
                  <p className="truncate text-xs text-muted">
                    {[device.platform, device.os_version].filter(Boolean).join(' ')}
                    {device.updated_at && ` · ${dateTime(device.updated_at)}`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => remove.mutate(device.hwid)}
                  disabled={remove.isPending}
                  className="shrink-0 text-muted transition-colors hover:text-danger"
                  aria-label="Удалить устройство"
                >
                  <Trash2 className="size-4" />
                </button>
              </li>
            ))}
          </ul>
          <Button
            type="button"
            variant="ghost"
            className="text-danger"
            onClick={() => reset.mutate()}
            disabled={reset.isPending}
          >
            Отвязать все устройства
          </Button>
        </>
      ) : (
        <p className="text-sm text-muted">Устройств пока нет.</p>
      )}
    </Section>
  )
}

export function UserEditor({ user, onClose }: { user: PanelUser; onClose: () => void }) {
  const queryClient = useQueryClient()
  const { data: squads } = useQuery({ queryKey: ['squads'], queryFn: panel.squads })

  const [form, setForm] = useState({
    expire_at: toLocalInput(user.expire_at),
    status: user.status === 'DISABLED' ? 'DISABLED' : 'ACTIVE',
    traffic_limit_gib: user.traffic_limit_bytes / GIB,
    traffic_limit_strategy: user.traffic_limit_strategy,
    hwid_device_limit: user.hwid_device_limit ?? 0,
    telegram_id: user.telegram_id?.toString() ?? '',
    email: user.email ?? '',
    tag: user.tag ?? '',
    description: user.description ?? '',
    squad_uuids: user.squads.map((s) => s.uuid),
  })

  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const save = useMutation({
    mutationFn: () => {
      const payload: UserUpdate = {
        status: form.status as 'ACTIVE' | 'DISABLED',
        traffic_limit_bytes: Math.round(form.traffic_limit_gib * GIB),
        traffic_limit_strategy: form.traffic_limit_strategy,
        hwid_device_limit: form.hwid_device_limit,
        email: form.email,
        tag: form.tag,
        description: form.description,
        squad_uuids: form.squad_uuids,
      }
      if (form.expire_at) payload.expire_at = new Date(form.expire_at).toISOString()
      if (form.telegram_id) payload.telegram_id = Number(form.telegram_id)
      return panel.updateUser(user.uuid, payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      queryClient.invalidateQueries({ queryKey: ['user-status-counts'] })
      onClose()
    },
  })

  const extend = useMutation({
    mutationFn: (days: number) => panel.extendUser(user.uuid, days),
    onSuccess: (updated) => {
      set('expire_at', toLocalInput(updated.expire_at))
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })

  const toggleSquad = (uuid: string) =>
    set(
      'squad_uuids',
      form.squad_uuids.includes(uuid)
        ? form.squad_uuids.filter((s) => s !== uuid)
        : [...form.squad_uuids, uuid],
    )

  const expiry = untilExpiry(user.expire_at)

  return (
    <Modal
      title={user.username}
      icon={<UserIcon className="size-5" />}
      onClose={onClose}
      footer={
        <>
          {save.isError && (
            <p className="mr-auto text-sm text-danger">{(save.error as Error).message}</p>
          )}
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? 'Сохраняю…' : 'Сохранить'}
          </Button>
        </>
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        {/* ── Сводка ─────────────────────────────────────────────── */}
        <Section icon={<UserIcon className="size-4" />} title="Обзор">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-xs text-muted">Подписка</dt>
              <dd className={expiry.expired ? 'text-danger' : ''}>{expiry.text}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Трафик</dt>
              <dd className="tabular-nums">{bytes(user.used_traffic_bytes)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Последний вход</dt>
              <dd>{user.online_at ? dateTime(user.online_at) : 'никогда'}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Создан</dt>
              <dd>{user.created_at ? dateTime(user.created_at) : '—'}</dd>
            </div>
          </dl>

          {user.subscription_url && (
            <div>
              <span className="text-xs text-muted">Ссылка подписки</span>
              <div className="mt-1 flex gap-2">
                <Input readOnly value={user.subscription_url} className="font-mono text-xs" />
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => navigator.clipboard.writeText(user.subscription_url!)}
                  aria-label="Скопировать ссылку"
                >
                  <Copy className="size-4" />
                </Button>
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            {[30, 90, 365].map((days) => (
              <Button
                key={days}
                type="button"
                variant="ghost"
                onClick={() => extend.mutate(days)}
                disabled={extend.isPending}
              >
                <CalendarPlus className="size-4" />+{days} дн.
              </Button>
            ))}
          </div>
        </Section>

        {/* ── Доступ ─────────────────────────────────────────────── */}
        <Section icon={<Shield className="size-4" />} title="Настройки доступа">
          <Field label="Подписка действует до">
            <Input
              type="datetime-local"
              value={form.expire_at}
              onChange={(e) => set('expire_at', e.target.value)}
            />
          </Field>

          <Field label="Статус">
            <select
              value={form.status}
              onChange={(e) => set('status', e.target.value)}
              className="h-11 w-full rounded-2xl border bg-bg/60 px-4 text-sm"
            >
              <option value="ACTIVE">Активен</option>
              <option value="DISABLED">Отключён</option>
            </select>
          </Field>

          <div>
            <span className="text-sm font-medium">Внутренние сквады</span>
            {squads?.length ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {squads.map((squad) => (
                  <button
                    key={squad.uuid}
                    type="button"
                    onClick={() => toggleSquad(squad.uuid)}
                    className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                      form.squad_uuids.includes(squad.uuid)
                        ? 'border-accent bg-accent/10 text-accent'
                        : 'border-border/60 hover:bg-surface-hover'
                    }`}
                  >
                    {squad.name}
                  </button>
                ))}
              </div>
            ) : (
              <p className="mt-1 text-xs text-muted">Сквады не загрузились.</p>
            )}
          </div>
        </Section>

        {/* ── Трафик ─────────────────────────────────────────────── */}
        <Section icon={<Gauge className="size-4" />} title="Трафик и лимиты">
          <Field label="Лимит трафика, ГиБ" hint="0 — без ограничений">
            <Input
              type="number"
              min={0}
              step="0.1"
              value={form.traffic_limit_gib}
              onChange={(e) => set('traffic_limit_gib', Number(e.target.value))}
            />
          </Field>

          <Field label="Стратегия сброса трафика">
            <select
              value={form.traffic_limit_strategy}
              onChange={(e) => set('traffic_limit_strategy', e.target.value)}
              className="h-11 w-full rounded-2xl border bg-bg/60 px-4 text-sm"
            >
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Лимит устройств (HWID)" hint="0 — без ограничений">
            <Input
              type="number"
              min={0}
              value={form.hwid_device_limit}
              onChange={(e) => set('hwid_device_limit', Number(e.target.value))}
            />
          </Field>
        </Section>

        {/* ── Контакты ───────────────────────────────────────────── */}
        <Section icon={<Mail className="size-4" />} title="Контакты и метки">
          <Field label="Telegram ID">
            <Input
              value={form.telegram_id}
              onChange={(e) => set('telegram_id', e.target.value.replace(/\D/g, ''))}
              inputMode="numeric"
              placeholder="123456789"
            />
          </Field>

          <Field label="E-mail">
            <Input
              type="email"
              value={form.email}
              onChange={(e) => set('email', e.target.value)}
              placeholder="client@example.com"
            />
          </Field>

          <Field label="Тег">
            <Input
              value={form.tag}
              onChange={(e) => set('tag', e.target.value.toUpperCase())}
              placeholder="TRIAL"
            />
          </Field>

          <Field label="Описание">
            <textarea
              value={form.description}
              onChange={(e) => set('description', e.target.value)}
              rows={2}
              className="w-full rounded-2xl border bg-bg/60 p-3 text-sm"
            />
          </Field>
        </Section>

        <div className="lg:col-span-2">
          <DevicesSection uuid={user.uuid} />
        </div>
      </div>
    </Modal>
  )
}
