import { Check, Copy } from 'lucide-react'
import {
  useEffect,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react'
import { cn } from '@/lib/cn'

export function Button({
  variant = 'primary',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'danger'
}) {
  return (
    <button
      className={cn(
        'inline-flex h-10 items-center justify-center gap-2 rounded-full px-5',
        'text-sm font-medium transition-all duration-200',
        'active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50',
        variant === 'primary' &&
          'bg-accent text-accent-fg shadow-lg shadow-accent/20 hover:brightness-110 hover:shadow-accent/30',
        variant === 'ghost' &&
          'border border-border/60 bg-surface/40 text-fg backdrop-blur-sm hover:bg-surface-hover',
        variant === 'danger' &&
          'bg-danger text-white shadow-lg shadow-danger/20 hover:brightness-110',
        className,
      )}
      {...props}
    />
  )
}

export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'h-11 w-full rounded-2xl border bg-bg/60 px-4 text-sm backdrop-blur-sm',
        'transition-colors placeholder:text-muted',
        'focus:border-accent/60',
        className,
      )}
      {...props}
    />
  )
}

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        'min-h-28 w-full rounded-2xl border bg-bg/60 px-4 py-3 text-sm backdrop-blur-sm',
        'transition-colors placeholder:text-muted',
        'focus:border-accent/60',
        className,
      )}
      {...props}
    />
  )
}

export function Card({
  className,
  children,
}: {
  className?: string
  children: ReactNode
}) {
  return (
    <div
      className={cn(
        'glass glass-sheen rounded-3xl p-6',
        'shadow-xl shadow-black/10',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  )
}

/**
 * Небольшая кнопка действия в строке списка: иконка и подпись. Раньше были
 * одни иконки — по стрелочке или карандашу не всегда понятно, что будет.
 */
export function RowAction({
  icon,
  label,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: ReactNode; label: string }) {
  return (
    <button
      type="button"
      title={label}
      className={cn(
        'inline-flex h-9 items-center gap-1.5 rounded-full border border-border/60 px-3 text-xs font-medium',
        'text-fg transition-colors hover:bg-surface-hover disabled:pointer-events-none disabled:opacity-50',
        className,
      )}
      {...props}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

/**
 * Удаление в два нажатия: первое превращает кнопку в красную «Точно
 * удалить?», второе удаляет. Без модальных окон, и случайный клик ничего
 * не сносит. Через 4 секунды без ответа кнопка возвращается обратно.
 */
export function DeleteAction({
  onConfirm,
  label = 'Удалить',
  confirmLabel = 'Точно удалить?',
  icon,
  disabled,
}: {
  onConfirm: () => void
  label?: string
  confirmLabel?: string
  icon: ReactNode
  disabled?: boolean
}) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const timer = window.setTimeout(() => setArmed(false), 4000)
    return () => window.clearTimeout(timer)
  }, [armed])

  return (
    <RowAction
      icon={icon}
      label={armed ? confirmLabel : label}
      disabled={disabled}
      className={armed ? 'border-danger bg-danger text-white hover:bg-danger' : 'text-danger'}
      onClick={() => {
        if (!armed) {
          setArmed(true)
          return
        }
        setArmed(false)
        onConfirm()
      }}
    />
  )
}

/** «Копировать» с подтверждением «Скопировано» — видно, что сработало. */
export function CopyAction({ text, label = 'Копировать' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(timer)
  }, [copied])
  return (
    <RowAction
      icon={copied ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}
      label={copied ? 'Скопировано' : label}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => setCopied(true))
      }}
    />
  )
}
