import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BotPage } from '@/components/BotShell'
import { SaveBar, SectionTitle, Toggle } from '@/components/page'
import { Button, Card, Field, Input, Textarea } from '@/components/ui'
import { panel, type BotStatus, type ButtonStyle } from '@/lib/api'
import { TelegramPreview, telegramHtml, type PreviewButton } from '@/components/telegram'
import { useBotSettingsDraft } from '@/lib/botSettings'
import { byKey, useBotCopy } from '@/lib/copy'
import { cn } from '@/lib/cn'

// Совпадает с MENU_BUTTONS в bot/app/keyboards.py. Третий элемент — можно
// ли кнопку скрыть (покупку/продление скрывать нельзя), четвёртый — есть ли
// она в главном меню (остальные живут на других экранах).
const MENU_BUTTONS: [string, string, boolean, boolean][] = [
  ['trial', 'Попробовать бесплатно', true, true],
  ['buy', 'Купить / Продлить', false, true],
  ['buy_more', 'Купить ещё одну', false, true],
  ['subscriptions', 'Подписка', true, true],
  ['connect', 'Подключиться', true, true],
  ['profile', 'Личный кабинет', true, true],
  ['balance', 'Баланс и пополнение', true, true],
  ['referral', 'Рефералка', true, true],
  ['promo', 'Промокод', true, true],
  ['channel', 'Наш канал', true, true],
  ['support', 'Поддержка', true, true],
  ['help', '«Помощь» и /help', true, false],
  ['history', 'История покупок (в кабинете)', true, false],
]

// Кнопки, у которых в боте есть цвет (у «Помощи» и истории его нет).
const COLORABLE = new Set([
  'trial', 'buy', 'buy_more', 'subscriptions', 'connect', 'profile', 'balance',
  'referral', 'promo', 'channel', 'support',
])
const DEFAULT_STYLES: Record<string, ButtonStyle> = { trial: 'success', referral: 'success' }
const STYLE_OPTIONS: [ButtonStyle, string, string][] = [
  ['none', 'Обычная', 'bg-surface-hover'],
  ['primary', 'Синяя', 'bg-sky-500'],
  ['success', 'Зелёная', 'bg-emerald-500'],
  ['danger', 'Красная', 'bg-rose-500'],
]
// Какие подписи правятся в строке: «Купить / Продлить» — две, у остальных одна.
const LABEL_KEYS: Record<string, [string, string][]> = {
  trial: [['trial', '']],
  buy: [
    ['buy', 'Нет подписки'],
    ['renew', 'Есть подписка'],
  ],
  buy_more: [['buy_more', '']],
  subscriptions: [['subscriptions', '']],
  connect: [['connect', '']],
  profile: [['profile', '']],
  balance: [['balance', '']],
  referral: [['referral', '']],
  promo: [['promo', '']],
  channel: [['channel', '']],
  support: [['support', '']],
}

const PREVIEW_VARS: Record<string, string> = { balance: '150 ₽' }

const SAMPLE_MENU = (hasSub: boolean) =>
  '{@rocket} <b>{brand}</b>\n\n👤 Иван\n' +
  (hasSub
    ? 'Подписка: <b>активна</b>\nДо: <b>30.11.2026</b> (12 дней)'
    : 'Подписка: <b>отсутствует</b>') +
  '\n\nВыберите действие:'

function MenuPhotoCard({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const refresh = (data: BotStatus) => queryClient.setQueryData(['bot'], data)
  const upload = useMutation({
    mutationFn: (file: File) => panel.setMenuPhoto(file),
    onSuccess: refresh,
    onError: (e: Error) => setError(e.message),
  })
  const remove = useMutation({ mutationFn: panel.deleteMenuPhoto, onSuccess: refresh })

  return (
    <Card className="space-y-4">
      <SectionTitle hint="Показывается над каждым экраном бота. JPEG, PNG или WebP до 5 МБ. Применяется сразу.">
        Картинка меню
      </SectionTitle>
      {status.menu_photo_url && (
        <img
          src={status.menu_photo_url}
          alt="Картинка меню"
          className="max-h-48 rounded-2xl border object-contain"
        />
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <label className="cursor-pointer">
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (!file) return
              setError(null)
              upload.mutate(file)
            }}
          />
          <span className="inline-flex h-10 items-center rounded-full bg-accent px-5 text-sm font-medium text-accent-fg">
            {upload.isPending ? 'Загружаю…' : status.menu_photo ? 'Заменить' : 'Загрузить'}
          </span>
        </label>
        {status.menu_photo && (
          <Button variant="ghost" onClick={() => remove.mutate()} disabled={remove.isPending}>
            Убрать
          </Button>
        )}
      </div>
    </Card>
  )
}

function EmojiCard({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => panel.setPremiumEmoji(enabled),
    onSuccess: (data) => queryClient.setQueryData(['bot'], data.status),
  })
  return (
    <Card className="space-y-2">
      <Toggle
        checked={status.emoji_mode === 'premium'}
        disabled={toggle.isPending}
        onChange={(value) => toggle.mutate(value)}
        label="Премиум-эмодзи"
        hint="Анимированные иконки на кнопках и в сообщениях. Нужен Telegram Premium у аккаунта, на котором создан бот. При включении бот пришлёт проверочное сообщение первому администратору бота — напишите боту /start заранее. Применяется сразу."
      />
      {toggle.isPending && <p className="text-sm text-muted">Проверяю…</p>}
      {toggle.data && (
        <p className={`text-sm ${toggle.data.ok ? 'text-success' : 'text-danger'}`}>
          {toggle.data.message}
        </p>
      )}
      {toggle.isError && <p className="text-sm text-danger">{(toggle.error as Error).message}</p>}
    </Card>
  )
}

function MenuEditor({ status }: { status: BotStatus }) {
  const draft = useBotSettingsDraft(status)
  const { form, set } = draft
  const styleOf = (key: string) => form.menu_styles[key] ?? DEFAULT_STYLES[key] ?? 'none'
  const visible = (key: string) => {
    if (form.menu_hidden.includes(key)) return false
    if (key === 'buy_more') return form.allow_multiple_subscriptions
    if (key === 'channel') return !!form.channel_url
    if (key === 'support') return !!form.support_url || status.support_mode === 'bot'
    return true
  }
  const { data: copy } = useBotCopy()
  const defaults = byKey(copy?.buttons)
  const [hasSub, setHasSub] = useState(false)

  const setLabel = (key: string, value: string) => {
    const next = { ...form.menu_labels }
    if (value.trim()) next[key] = value
    else delete next[key]
    set('menu_labels', next)
  }
  const labelOf = (key: string) => {
    let text = form.menu_labels[key]?.trim() || defaults[key]?.default || key
    for (const [name, value] of Object.entries(PREVIEW_VARS)) text = text.replace(`{${name}}`, value)
    return text
  }

  // Те же правила, что в боте (keyboards.main_menu): что показывается новому
  // клиенту и что — тому, у кого уже есть подписка.
  const previewButtons: PreviewButton[] = []
  const add = (styleKey: string, labelKey: string) =>
    previewButtons.push({ text: labelOf(labelKey), style: styleOf(styleKey) })
  if (!hasSub && visible('trial')) add('trial', 'trial')
  if (hasSub) {
    add('buy', 'renew')
    if (visible('buy_more')) add('buy_more', 'buy_more')
    if (visible('subscriptions')) add('subscriptions', 'subscriptions')
    if (visible('connect')) add('connect', 'connect')
  } else {
    add('buy', 'buy')
  }
  for (const key of ['profile', 'balance', 'referral', 'promo', 'channel', 'support']) {
    if (visible(key)) add(key, key)
  }
  const menuHtml = telegramHtml(
    form.welcome_text || SAMPLE_MENU(hasSub),
    { brand: status.bot_name || 'VPN' },
    copy?.icons ?? {},
  )

  const toggleMenu = (key: string) =>
    set(
      'menu_hidden',
      form.menu_hidden.includes(key)
        ? form.menu_hidden.filter((k) => k !== key)
        : [...form.menu_hidden, key],
    )

  return (
    <>
      <Card className="space-y-4">
        <SectionTitle hint="Первый экран бота. Пусто — стандартный текст с датой подписки. Можно HTML-разметку и {brand} — название бота.">
          Приветствие
        </SectionTitle>
        <Textarea
          value={form.welcome_text ?? ''}
          onChange={(e) => set('welcome_text', e.target.value || null)}
          placeholder={'{brand}\n\nБыстрый и безопасный VPN. Выберите тариф или попробуйте бесплатно.'}
          rows={4}
        />
      </Card>

      <Card className="space-y-5">
        <SectionTitle hint="Включайте кнопки, меняйте подписи и цвета — справа сразу видно, как меню выглядит у клиента. Пустая подпись — стандартная.">
          Кнопки меню
        </SectionTitle>

        <div className="grid gap-5 lg:grid-cols-[1fr_17rem]">
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted">Кнопок в ряд:</span>
              {[1, 2, 3].map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => set('menu_columns', n)}
                  className={cn(
                    'size-9 rounded-full border text-sm transition-colors',
                    form.menu_columns === n
                      ? 'border-accent bg-accent/10 font-medium text-accent'
                      : 'border-border/60 hover:bg-surface-hover',
                  )}
                >
                  {n}
                </button>
              ))}
            </div>
            <div className="divide-y divide-border/60 rounded-2xl border border-border/60">
              {MENU_BUTTONS.map(([key, label, hideable]) => {
                const labels = LABEL_KEYS[key] ?? []
                return (
                  <div key={key} className="space-y-2 px-3 py-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          className="size-4 accent-[var(--color-accent)]"
                          checked={!form.menu_hidden.includes(key)}
                          disabled={!hideable}
                          onChange={() => toggleMenu(key)}
                        />
                        {label}
                      </label>
                      {COLORABLE.has(key) && (
                        <div className="flex gap-1" role="radiogroup" aria-label={`Цвет: ${label}`}>
                          {STYLE_OPTIONS.map(([style, title, swatch]) => (
                            <button
                              key={style}
                              type="button"
                              title={title}
                              aria-label={title}
                              onClick={() => set('menu_styles', { ...form.menu_styles, [key]: style })}
                              className={cn(
                                'size-6 rounded-full border-2',
                                swatch,
                                styleOf(key) === style ? 'border-fg' : 'border-transparent opacity-60',
                              )}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                    {labels.length > 0 && (
                      <div className={cn('grid gap-2', labels.length > 1 && 'sm:grid-cols-2')}>
                        {labels.map(([labelKey, caption]) => (
                          <label key={labelKey} className="block">
                            {caption && <span className="mb-1 block text-xs text-muted">{caption}</span>}
                            <Input
                              className="h-9 rounded-xl"
                              maxLength={64}
                              value={form.menu_labels[labelKey] ?? ''}
                              placeholder={defaults[labelKey]?.default ?? ''}
                              onChange={(e) => setLabel(labelKey, e.target.value)}
                              aria-label={`Подпись: ${label}`}
                            />
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
            <p className="text-xs text-muted">
              В подписи баланса можно оставить <code>{'{balance}'}</code> — туда подставится сумма
              клиента. «Наш канал» и «Поддержка» появятся, когда указаны их ссылки; «Купить ещё
              одну» — если в «Тарифах» разрешено несколько подписок.
            </p>
          </div>

          <div className="space-y-2 lg:sticky lg:top-6 lg:self-start">
            <p className="text-xs font-medium uppercase tracking-wider text-muted">
              Так увидит клиент
            </p>
            <div className="flex gap-1 rounded-full border border-border/60 p-1 text-xs">
              {(
                [
                  [false, 'Новый клиент'],
                  [true, 'С подпиской'],
                ] as const
              ).map(([value, title]) => (
                <button
                  key={title}
                  type="button"
                  onClick={() => setHasSub(value)}
                  className={cn(
                    'flex-1 rounded-full px-3 py-1.5 transition-colors',
                    hasSub === value ? 'bg-accent/15 font-medium text-accent' : 'text-muted',
                  )}
                >
                  {title}
                </button>
              ))}
            </div>
            <TelegramPreview
              name={status.bot_name || 'Ваш бот'}
              html={menuHtml}
              buttons={previewButtons}
              columns={form.menu_columns}
            />
          </div>
        </div>
      </Card>

      <Card className="space-y-4">
        <SectionTitle hint="Кнопки, которые есть почти на каждом экране бота.">
          Кнопки «Назад» и «Отмена»
        </SectionTitle>
        <div className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ['back', 'Назад'],
              ['cancel', 'Отмена'],
            ] as const
          ).map(([labelKey, title]) => (
            <Field key={labelKey} label={title}>
              <Input
                maxLength={64}
                value={form.menu_labels[labelKey] ?? ''}
                placeholder={defaults[labelKey]?.default ?? title}
                onChange={(e) => setLabel(labelKey, e.target.value)}
              />
            </Field>
          ))}
        </div>
      </Card>

      <Card className="space-y-4">
        <SectionTitle hint="«Меню» и «Помощь» — где их показывать клиенту.">
          Кнопки «Меню» и «Помощь»
        </SectionTitle>
        <div className="grid gap-2 sm:grid-cols-2">
          {(
            [
              [
                'commands',
                'В кнопке меню слева · рекомендуем',
                'Кнопка «Меню» Telegram рядом с полем ввода. Никаких лишних сообщений в чате, не мешает жесту «назад».',
              ],
              [
                'keyboard',
                'Под полем ввода',
                'Две кнопки под строкой сообщения. Telegram ставит их только вместе с сообщением, поэтому бот один раз за всё время присылает невидимое (пустой пузырёк).',
              ],
            ] as const
          ).map(([mode, title, hint]) => (
            <button
              key={mode}
              type="button"
              onClick={() => set('menu_mode', mode)}
              className={cn(
                'rounded-2xl border px-3 py-2 text-left text-sm transition-colors',
                form.menu_mode === mode
                  ? 'border-accent bg-accent/10'
                  : 'border-border/60 hover:bg-surface-hover',
              )}
            >
              <span className="font-medium">{title}</span>
              <span className="mt-0.5 block text-xs text-muted">{hint}</span>
            </button>
          ))}
        </div>
        <p className="text-xs text-muted">
          У клиентов кнопки сменятся, когда они в следующий раз нажмут /start или «Меню».
        </p>
      </Card>

      <Card className="space-y-4">
        <SectionTitle
          hint={
            <>
              Пустое поле — кнопки не будет. Поддержка настраивается на{' '}
              <Link to="/bot/support" className="text-accent hover:underline">
                отдельной странице
              </Link>
              .
            </>
          }
        >
          Ссылки
        </SectionTitle>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Наш канал">
            <Input
              value={form.channel_url ?? ''}
              onChange={(e) => set('channel_url', e.target.value || null)}
              placeholder="https://t.me/channel"
            />
          </Field>
          <Field label="Политика конфиденциальности">
            <Input
              value={form.privacy_policy_url ?? ''}
              onChange={(e) => set('privacy_policy_url', e.target.value || null)}
              placeholder="https://..."
            />
          </Field>
          <Field label="Пользовательское соглашение">
            <Input
              value={form.terms_url ?? ''}
              onChange={(e) => set('terms_url', e.target.value || null)}
              placeholder="https://..."
            />
          </Field>
        </div>
      </Card>

      <SaveBar
        dirty={draft.dirty}
        pending={draft.save.isPending}
        error={draft.error}
        savedAt={draft.savedAt}
        onSave={() => draft.save.mutate(undefined)}
        onReset={draft.reset}
      />
    </>
  )
}

export default function BotMenu() {
  return (
    <BotPage
      title="Меню и кнопки"
      description="Главное меню бота: приветствие, картинка, кнопки — их подписи, цвета и порядок."
    >
      {(status) => (
        <>
          <MenuEditor status={status} />
          <MenuPhotoCard status={status} />
          <EmojiCard status={status} />
        </>
      )}
    </BotPage>
  )
}
