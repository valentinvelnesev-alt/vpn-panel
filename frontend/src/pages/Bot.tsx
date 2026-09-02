import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Play, Square, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type BotStatus, type BotSettings } from '@/lib/api'
import BotPlans from './BotPlans'
import BotPromo from './BotPromo'
import BotReferral from './BotReferral'

const STATE_LABEL: Record<BotStatus['state'], [string, string]> = {
  running: ['bg-success', 'работает'],
  stopped: ['bg-muted', 'остановлен'],
  error: ['bg-danger', 'ошибка'],
}

function TokenCard({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const [token, setToken] = useState('')

  const refresh = (data: BotStatus) => queryClient.setQueryData(['bot'], data)

  const check = useMutation({ mutationFn: () => panel.checkBotToken(token) })
  const save = useMutation({
    mutationFn: () => panel.setBotToken(token),
    onSuccess: (data) => {
      refresh(data)
      setToken('')
      check.reset()
    },
  })
  const toggle = useMutation({
    mutationFn: () => (status.enabled ? panel.stopBot() : panel.startBot()),
    onSuccess: refresh,
  })

  // enabled=true, но состояние ещё «остановлен» — команда ушла супервизору,
  // но он не успел отчитаться. Без этого пользователь видит «остановлен»
  // сразу после нажатия «Запустить» и решает, что кнопка не сработала.
  const starting = status.enabled && status.state === 'stopped'
  const [colour, label] = starting ? ['bg-warning animate-pulse', 'запускается…'] : STATE_LABEL[status.state]
  const replacing = status.configured

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium">Подключение бота</h2>
          <p className="mt-1 text-sm text-muted">
            Создайте бота в{' '}
            <a
              href="https://t.me/BotFather"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              @BotFather
            </a>{' '}
            и вставьте сюда его токен.
          </p>
        </div>
        <span className="flex shrink-0 items-center gap-2 text-sm">
          <span className={`size-2 rounded-full ${colour}`} aria-hidden />
          {label}
        </span>
      </div>

      {status.configured && (
        <div className="mt-4 rounded-2xl bg-bg px-3 py-2 text-sm">
          <span className="font-medium">@{status.bot_username}</span>
          <span className="text-muted"> · токен {status.token_masked}</span>
        </div>
      )}

      {status.state === 'error' && status.state_message && (
        <p className="mt-3 rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger">
          {status.state_message}
        </p>
      )}

      <form
        className="mt-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <Field
          label={replacing ? 'Новый токен' : 'Токен бота'}
          hint={
            replacing
              ? 'Смена бота не удаляет клиентов: они привязаны к Telegram ID. Но новый бот не сможет написать тем, кто его ещё не запускал — предупредите их через старого бота заранее.'
              : 'Токен хранится в базе в зашифрованном виде.'
          }
        >
          <Input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="123456789:AA..."
            autoComplete="off"
          />
        </Field>

        {check.data && (
          <p
            className={`flex items-center gap-2 text-sm ${
              check.data.ok ? 'text-success' : 'text-danger'
            }`}
          >
            {check.data.ok ? (
              <CheckCircle2 className="size-4" />
            ) : (
              <XCircle className="size-4" />
            )}
            {check.data.message}
          </p>
        )}
        {save.isError && (
          <p className="text-sm text-danger">{(save.error as Error).message}</p>
        )}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!token || save.isPending}>
            {replacing ? 'Применить' : 'Сохранить'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => check.mutate()}
            disabled={!token || check.isPending}
          >
            {check.isPending ? 'Проверяю…' : 'Проверить'}
          </Button>

          {status.configured && (
            <Button
              type="button"
              variant={status.enabled ? 'danger' : 'primary'}
              className="ml-auto"
              onClick={() => toggle.mutate()}
              disabled={toggle.isPending || starting}
            >
              {status.enabled ? (
                <>
                  <Square className="size-4" /> {starting ? 'Запускается…' : 'Остановить'}
                </>
              ) : (
                <>
                  <Play className="size-4" /> Запустить
                </>
              )}
            </Button>
          )}
        </div>
      </form>
    </Card>
  )
}

function SettingsCard({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<BotSettings>(status)
  const { data: squads } = useQuery({ queryKey: ['squads'], queryFn: panel.squads })

  useEffect(() => setForm(status), [status])

  const save = useMutation({
    mutationFn: () => panel.saveBotSettings(form),
    onSuccess: (data) => queryClient.setQueryData(['bot'], data),
  })

  const set = <K extends keyof BotSettings>(key: K, value: BotSettings[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const toggleTrialSquad = (uuid: string) =>
    set(
      'trial_squad_uuids',
      form.trial_squad_uuids.includes(uuid)
        ? form.trial_squad_uuids.filter((s) => s !== uuid)
        : [...form.trial_squad_uuids, uuid],
    )

  return (
    <Card>
      <h2 className="font-medium">Поведение бота</h2>

      <form
        className="mt-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Ссылка на поддержку">
            <Input
              value={form.support_url ?? ''}
              onChange={(e) => set('support_url', e.target.value)}
              placeholder="https://t.me/support"
            />
          </Field>
          <Field label="Ссылка на канал">
            <Input
              value={form.channel_url ?? ''}
              onChange={(e) => set('channel_url', e.target.value)}
              placeholder="https://t.me/channel"
            />
          </Field>
        </div>

        <Field
          label="ID канала для проверки подписки"
          hint="Например @mychannel. Бот должен быть админом канала."
        >
          <Input
            value={form.channel_id ?? ''}
            onChange={(e) => set('channel_id', e.target.value)}
            placeholder="@mychannel"
          />
        </Field>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={form.require_channel_sub}
            onChange={(e) => set('require_channel_sub', e.target.checked)}
          />
          Требовать подписку на канал
        </label>

        <hr />

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={form.trial_enabled}
            onChange={(e) => set('trial_enabled', e.target.checked)}
          />
          Пробный период
        </label>

        {form.trial_enabled && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Дней пробного периода">
              <Input
                type="number"
                min={1}
                value={form.trial_days}
                onChange={(e) => set('trial_days', Number(e.target.value))}
              />
            </Field>
            <Field label="Лимит устройств на триале">
              <Input
                type="number"
                min={1}
                value={form.trial_hwid_limit}
                onChange={(e) => set('trial_hwid_limit', Number(e.target.value))}
              />
            </Field>
          </div>
        )}

        {form.trial_enabled && (
          <div>
            <span className="text-sm font-medium">Сквады Remnawave для триала</span>
            {squads?.length ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {squads.map((squad) => (
                  <button
                    key={squad.uuid}
                    type="button"
                    onClick={() => toggleTrialSquad(squad.uuid)}
                    className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                      form.trial_squad_uuids.includes(squad.uuid)
                        ? 'border-accent bg-accent/10 text-accent'
                        : 'hover:bg-surface-hover'
                    }`}
                  >
                    {squad.name}
                  </button>
                ))}
              </div>
            ) : (
              <p className="mt-1 text-xs text-muted">
                Сквады не загрузились — проверьте подключение к Remnawave.
              </p>
            )}
            {form.trial_squad_uuids.length === 0 && (
              <p className="mt-1 text-xs text-danger">
                Не выбран ни один сквад — триал-подписки будут создаваться без доступа
                ни к одной ноде.
              </p>
            )}
          </div>
        )}

        <hr />

        <Field label="Чат для уведомлений о продажах (ID, необязательно)">
          <Input
            type="number"
            placeholder="например -1001234567890"
            value={form.purchase_notify_chat_id ?? ''}
            onChange={(e) =>
              set(
                'purchase_notify_chat_id',
                e.target.value ? Number(e.target.value) : null
              )
            }
          />
        </Field>
        <p className="text-sm text-muted">
          Бот должен быть добавлен в этот чат/группу. Оставьте пустым, чтобы
          отключить уведомления о продажах.
        </p>

        <Field
          label="Администраторы бота (Telegram ID через запятую)"
          hint="Этим ID в самом боте открывается команда /admin со статистикой. Свой ID можно узнать у @userinfobot."
        >
          <Input
            value={form.admin_telegram_ids.join(', ')}
            onChange={(e) =>
              set(
                'admin_telegram_ids',
                e.target.value
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean)
                  .map(Number)
                  .filter((n) => Number.isFinite(n)),
              )
            }
            placeholder="123456789, 987654321"
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Ссылка на политику конфиденциальности">
            <Input
              value={form.privacy_policy_url ?? ''}
              onChange={(e) => set('privacy_policy_url', e.target.value || null)}
              placeholder="https://..."
            />
          </Field>
          <Field label="Ссылка на пользовательское соглашение">
            <Input
              value={form.terms_url ?? ''}
              onChange={(e) => set('terms_url', e.target.value || null)}
              placeholder="https://..."
            />
          </Field>
        </div>
        <p className="text-sm text-muted">
          Если заполнены — в профиле бота появятся кнопки со ссылками. Пусто — кнопок не будет.
        </p>

        {save.isSuccess && <p className="text-sm text-success">Сохранено</p>}
        {save.isError && (
          <p className="text-sm text-danger">{(save.error as Error).message}</p>
        )}

        <Button type="submit" disabled={save.isPending}>
          Сохранить
        </Button>
      </form>
    </Card>
  )
}

export default function Bot() {
  const { data: status, isPending } = useQuery({
    queryKey: ['bot'],
    queryFn: panel.botStatus,
    // Пока супервизор ещё не отчитался (команда ушла, а состояние в БД
    // всё ещё «остановлен») — опрашиваем часто, чтобы увидеть переход в
    // «работает» почти сразу, а не ждать обычные 15 секунд.
    refetchInterval: (query) => {
      const data = query.state.data
      const starting = !!data && data.enabled && data.state === 'stopped'
      return starting ? 1500 : 15_000
    },
  })

  if (isPending || !status) return <p className="text-sm text-muted">Загрузка…</p>

  return (
    <div className="max-w-3xl space-y-6">
      <TokenCard status={status} />
      {status.configured && (
        <>
          <BotPlans />
          <BotPromo />
          <BotReferral />
          <SettingsCard status={status} />
        </>
      )}
    </div>
  )
}
