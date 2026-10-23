import { Check, Loader2, RotateCcw, TriangleAlert } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui'
import { cn } from '@/lib/cn'
import type { Applied } from '@/lib/applied'
import { useUnsavedGuard } from '@/lib/unsaved'

/** Заголовок раздела: название, одна строка пояснения и действия справа. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/**
 * Панель сохранения, прилипшая к низу экрана. Появляется, как только на
 * странице есть изменения, — не нужно листать к кнопке в конце формы.
 * После сохранения коротко подтверждает, что бот уже применил настройки:
 * многие думали, что его надо перезапускать.
 */
export function SaveBar({
  dirty,
  pending,
  error,
  savedAt,
  onSave,
  onReset,
  savedNote = 'Сохранено',
  applied,
}: {
  dirty: boolean
  pending: boolean
  error?: string | null
  savedAt?: number
  onSave: () => void
  onReset: () => void
  savedNote?: string
  /** Подтверждение от бота (lib/applied.ts); без него — просто «Сохранено». */
  applied?: Applied
}) {
  useUnsavedGuard(dirty)
  const [showSaved, setShowSaved] = useState(false)

  useEffect(() => {
    if (!savedAt) return
    setShowSaved(true)
    // Сообщение о результате держим дольше, чем простое «Сохранено».
    const timer = window.setTimeout(
      () => setShowSaved(false),
      applied?.state === 'stale' ? 12000 : applied?.state === 'applied' ? 7000 : 4000,
    )
    return () => window.clearTimeout(timer)
  }, [savedAt, applied?.state])

  const visible = dirty || pending || !!error || showSaved

  return (
    <>
      <div
        className={cn(
          // На компьютере — по центру рабочей области, а не поверх меню слева.
          'fixed inset-x-0 bottom-4 z-30 flex justify-center px-4 transition-all duration-300 lg:pl-[19.5rem]',
          visible ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-6 opacity-0',
        )}
      >
        <div className="flex w-full max-w-2xl flex-wrap items-center gap-3 rounded-3xl border border-accent/30 bg-surface px-4 py-3 shadow-2xl shadow-black/50">
          {dirty || pending || error ? (
            <>
              <span className="flex-1 text-sm">
                {error ? (
                  <span className="text-danger">{error}</span>
                ) : (
                  <>
                    <span className="mr-2 inline-block size-2 rounded-full bg-warning" />
                    Есть несохранённые изменения
                  </>
                )}
              </span>
              <Button variant="ghost" disabled={pending || !dirty} onClick={onReset}>
                <RotateCcw className="size-4" />
                Отменить
              </Button>
              <Button disabled={pending || !dirty} onClick={onSave}>
                {pending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
                Сохранить
              </Button>
            </>
          ) : (
            <AppliedNote applied={applied} savedNote={savedNote} />
          )}
        </div>
      </div>
    </>
  )
}

function AppliedNote({ applied, savedNote }: { applied?: Applied; savedNote: string }) {
  if (applied?.state === 'waiting') {
    return (
      <span className="flex flex-1 items-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 shrink-0 animate-spin" />
        Сохранено, бот применяет настройки…
      </span>
    )
  }
  if (applied?.state === 'stale') {
    return (
      <span className="flex flex-1 items-start gap-2 text-sm text-warning">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" />
        <span>
          Сохранено, но бот не подтвердил, что получил настройки. Проверьте, что он запущен
          («Бот → Подключение»): он подхватит их сам, как только ответит.
        </span>
      </span>
    )
  }
  if (applied?.state === 'applied') {
    const time = applied.at
      ? new Date(applied.at).toLocaleTimeString('ru-RU', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      : ''
    return (
      <span className="flex flex-1 items-start gap-2 text-sm text-success">
        <Check className="mt-0.5 size-4 shrink-0" />
        <span>
          Бот применил настройки{time && ` в ${time}`}. Перезапускать не нужно. Сообщения, уже
          открытые в чате, сами не меняются — у клиентов новое появится после /start или
          «Назад».
        </span>
      </span>
    )
  }
  return (
    <span className="flex flex-1 items-center gap-2 text-sm text-success">
      <Check className="size-4 shrink-0" />
      {savedNote}
    </span>
  )
}

/** Подзаголовок блока внутри карточки. */
export function SectionTitle({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div>
      <h2 className="font-medium">{children}</h2>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
    </div>
  )
}

/** Переключатель вместо голого чекбокса — заметнее и понятнее «вкл/выкл». */
export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  label: ReactNode
  hint?: ReactNode
  disabled?: boolean
}) {
  return (
    <label className={cn('flex items-start gap-3', disabled ? 'opacity-60' : 'cursor-pointer')}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative mt-0.5 h-6 w-10 shrink-0 rounded-full transition-colors',
          checked ? 'bg-accent' : 'bg-surface-hover ring-1 ring-border',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 size-5 rounded-full bg-white shadow transition-all',
            checked ? 'left-[1.125rem]' : 'left-0.5',
          )}
        />
      </button>
      <span className="min-w-0 text-sm">
        <span className="font-medium">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted">{hint}</span>}
      </span>
    </label>
  )
}
