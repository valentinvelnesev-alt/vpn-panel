import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Play, Sparkles, Square, XCircle } from 'lucide-react'
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

  const [colour, label] = STATE_LABEL[status.state]
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
              disabled={toggle.isPending}
            >
              {status.enabled ? (
                <>
                  <Square className="size-4" /> Остановить
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

function EmojiCard({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const [chatId, setChatId] = useState('')
  const [ids, setIds] = useState(
    () => JSON.stringify(status.premium_emoji, null, 2) || '{}',
  )

  const apply = useMutation({
    mutationFn: (mode: 'plain' | 'premium') =>
      panel.setEmojiMode({
        mode,
        premium_emoji: mode === 'premium' ? JSON.parse(ids || '{}') : {},
        test_chat_id: chatId ? Number(chatId) : undefined,
      }),
    onSuccess: (data) => queryClient.setQueryData(['bot'], data.status),
  })

  const premium = status.emoji_mode === 'premium'

  return (
    <Card>
      <div className="flex items-center gap-2">
        <Sparkles className="size-4 text-accent" />
        <h2 className="font-medium">Эмодзи в сообщениях</h2>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {(
          [
            ['plain', 'Обычные', 'Работают у всех и всегда'],
            ['premium', 'Премиум', 'Анимированные кастомные эмодзи'],
          ] as const
        ).map(([mode, title, hint]) => (
          <button
            key={mode}
            type="button"
            onClick={() => apply.mutate(mode)}
            disabled={apply.isPending}
            className={`rounded-2xl border p-3 text-left transition-colors ${
              (mode === 'premium') === premium
                ? 'border-accent bg-accent/10'
                : 'hover:bg-surface-hover'
            }`}
          >
            <div className="text-sm font-medium">{title}</div>
            <div className="text-xs text-muted">{hint}</div>
          </button>
        ))}
      </div>

      <p className="mt-4 rounded-2xl bg-warning/10 px-3 py-2 text-xs text-warning">
        Если хотите с премиум-эмодзи — на аккаунте, на котором бот создан в
        @BotFather, должен быть активен Telegram Premium. Панель проверит это:
        отправит боту тестовое сообщение и включит режим, только если Telegram
        его примет.
      </p>

      <div className="mt-4 space-y-4">
        <Field
          label="Ваш Telegram ID"
          hint="Куда прислать проверочное сообщение. Напишите боту /start, иначе он не сможет вам ответить."
        >
          <Input
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            inputMode="numeric"
            placeholder="123456789"
          />
        </Field>

        <Field
          label="Соответствие иконок и премиум-эмодзи"
          hint="JSON вида {&quot;shield&quot;: &quot;5237699328843200968&quot;}. ID берётся из пересланного сообщения с нужным эмодзи — универсальных значений не существует."
        >
          <textarea
            value={ids}
            onChange={(e) => setIds(e.target.value)}
            rows={4}
            spellCheck={false}
            className="w-full rounded-2xl border bg-bg p-3 font-mono text-xs"
          />
        </Field>
      </div>

      {apply.data && (
        <p
          className={`mt-3 text-sm ${
            apply.data.ok ? 'text-success' : 'text-danger'
          }`}
        >
          {apply.data.message}
        </p>
      )}
      {apply.isError && (
        <p className="mt-3 text-sm text-danger">{(apply.error as Error).message}</p>
      )}
    </Card>
  )
}

function SettingsCard({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<BotSettings>(status)

  useEffect(() => setForm(status), [status])

  const save = useMutation({
    mutationFn: () => panel.saveBotSettings(form),
    onSuccess: (data) => queryClient.setQueryData(['bot'], data),
  })

  const set = <K extends keyof BotSettings>(key: K, value: BotSettings[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

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
        <Field
          label="Приветствие"
          hint="Можно использовать {@shield}, {@gift} и другие иконки, а также {brand}."
        >
          <textarea
            value={form.welcome_text ?? ''}
            onChange={(e) => set('welcome_text', e.target.value)}
            rows={3}
            className="w-full rounded-2xl border bg-bg p-3 text-sm"
            placeholder="{@shield} Добро пожаловать!"
          />
        </Field>

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
    refetchInterval: 15_000,
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
          <EmojiCard status={status} />
          <SettingsCard status={status} />
        </>
      )}
    </div>
  )
}
