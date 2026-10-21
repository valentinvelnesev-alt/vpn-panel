import { Check, Loader2, RotateCcw } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui'
import { cn } from '@/lib/cn'
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
  savedNote = 'Сохранено — бот уже работает с новыми настройками, перезапускать не нужно',
}: {
  dirty: boolean
  pending: boolean
  error?: string | null
  savedAt?: number
  onSave: () => void
  onReset: () => void
  savedNote?: string
}) {
  useUnsavedGuard(dirty)
  const [showSaved, setShowSaved] = useState(false)

  useEffect(() => {
    if (!savedAt) return
    setShowSaved(true)
    const timer = window.setTimeout(() => setShowSaved(false), 4000)
    return () => window.clearTimeout(timer)
  }, [savedAt])

  const visible = dirty || pending || !!error || showSaved

  return (
    <>
      <div
        className={cn(
          'fixed inset-x-0 bottom-4 z-30 flex justify-center px-4 transition-all duration-300',
          visible ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-6 opacity-0',
        )}
      >
        <div className="glass flex w-full max-w-2xl flex-wrap items-center gap-3 rounded-3xl px-4 py-3 shadow-2xl shadow-black/30">
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
            <span className="flex flex-1 items-center gap-2 text-sm text-success">
              <Check className="size-4 shrink-0" />
              {savedNote}
            </span>
          )}
        </div>
      </div>
    </>
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
