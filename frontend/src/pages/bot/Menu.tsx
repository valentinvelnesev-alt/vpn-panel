import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { BotPage } from '@/components/BotShell'
import { SaveBar, SectionTitle, Toggle } from '@/components/page'
import { Button, Card, Field, Input, Textarea } from '@/components/ui'
import { panel, type BotStatus, type ButtonStyle } from '@/lib/api'
import { useBotSettingsDraft } from '@/lib/botSettings'
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
const PREVIEW_STYLE: Record<ButtonStyle, string> = {
  none: 'bg-white/10 text-fg',
  primary: 'bg-sky-500/90 text-white',
  success: 'bg-emerald-500/90 text-white',
  danger: 'bg-rose-500/90 text-white',
}

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
    if (key === 'support') return !!form.support_url
    return true
  }
  const preview = MENU_BUTTONS.filter(([key, , , inMenu]) => inMenu && visible(key))

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
        <SectionTitle hint="Включайте и выключайте кнопки и выбирайте их цвет — справа видно, как меню выглядит у клиента.">
          Кнопки меню
        </SectionTitle>

        <div className="grid gap-5 md:grid-cols-[1fr_15rem]">
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
              {MENU_BUTTONS.map(([key, label, hideable]) => (
                <div
                  key={key}
                  className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                >
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
              ))}
            </div>
            <p className="text-xs text-muted">
              «Наш канал» и «Поддержка» появятся, когда ниже указаны их ссылки; «Купить ещё
              одну» — если в «Тарифах» разрешено несколько подписок.
            </p>
          </div>

          <div className="md:sticky md:top-6 md:self-start">
            <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
              Так увидит клиент
            </p>
            <div className="rounded-3xl bg-[#17212b] p-3 shadow-inner">
              <div className="mb-2 rounded-2xl bg-[#182533] px-3 py-2 text-xs text-white/80">
                {status.bot_name || 'Ваш бот'}
                <br />
                <span className="text-white/50">Выберите действие:</span>
              </div>
              <div
                className="grid gap-1"
                style={{ gridTemplateColumns: `repeat(${form.menu_columns}, minmax(0, 1fr))` }}
              >
                {preview.map(([key, label]) => (
                  <span
                    key={key}
                    className={cn(
                      'truncate rounded-lg px-2 py-1.5 text-center text-[11px]',
                      PREVIEW_STYLE[styleOf(key)],
                    )}
                  >
                    {label.replace(' / Продлить', '')}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </Card>

      <Card className="space-y-4">
        <SectionTitle hint="«Меню» и «Помощь» — где их показывать клиенту.">
          Кнопки «Меню» и «Помощь»
        </SectionTitle>
        <div className="grid gap-2 sm:grid-cols-2">
          {(
            [
              ['keyboard', 'Под полем ввода', 'Две кнопки под строкой сообщения.'],
              [
                'commands',
                'В кнопке меню слева',
                'Кнопка «Меню» Telegram рядом с полем ввода — не мешает жесту «назад».',
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
          У клиентов клавиатура сменится, когда они в следующий раз нажмут /start или «Меню».
        </p>
      </Card>

      <Card className="space-y-4">
        <SectionTitle hint="Пустое поле — кнопки не будет.">Ссылки</SectionTitle>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Поддержка">
            <Input
              value={form.support_url ?? ''}
              onChange={(e) => set('support_url', e.target.value || null)}
              placeholder="https://t.me/support"
            />
          </Field>
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
      title="Меню и тексты"
      description="Что клиент видит в боте: приветствие, картинка, кнопки и их цвета."
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
