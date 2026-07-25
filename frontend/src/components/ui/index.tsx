import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
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
