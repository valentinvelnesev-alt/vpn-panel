import { X } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * Модальное окно поверх страницы.
 *
 * Рендерится в портал прямо в body: иначе `backdrop-filter` родительской
 * стеклянной карточки создаёт containing block и position:fixed внутри
 * прижимается к карточке, а не к экрану.
 */
export function Modal({
  title,
  icon,
  onClose,
  children,
  footer,
}: {
  title: string
  icon?: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    // Фон не должен прокручиваться под открытым окном.
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [onClose])

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 backdrop-blur-sm sm:p-8"
      onClick={onClose}
    >
      <div
        className="glass glass-sheen my-auto w-full max-w-4xl rounded-3xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center gap-3 border-b border-border/60 px-6 py-4">
          {icon && (
            <span className="grid size-9 place-items-center rounded-2xl bg-accent/15 text-accent">
              {icon}
            </span>
          )}
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            onClick={onClose}
            className="ml-auto rounded-full p-2 text-muted transition-colors hover:bg-surface-hover hover:text-fg"
            aria-label="Закрыть"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto p-6">{children}</div>

        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-border/60 px-6 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
