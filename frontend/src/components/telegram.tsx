import { useMemo } from 'react'
import { cn } from '@/lib/cn'

/**
 * Превью сообщений бота «как в Telegram»: пузырь с форматированием и
 * inline-кнопки под ним. Разметка администратора не вставляется в страницу
 * как есть — текст разбирается и собирается заново только из разрешённых
 * тегов, так что чужой HTML на панель не попадёт.
 */

const escapeHtml = (value: string) =>
  value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function serialize(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return escapeHtml(node.textContent ?? '').replace(/\n/g, '<br>')
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return ''
  const el = node as Element
  const inner = Array.from(el.childNodes).map(serialize).join('')
  switch (el.tagName.toLowerCase()) {
    case 'b':
    case 'strong':
      return `<b>${inner}</b>`
    case 'i':
    case 'em':
      return `<i>${inner}</i>`
    case 'u':
    case 'ins':
      return `<u>${inner}</u>`
    case 's':
    case 'strike':
    case 'del':
      return `<s>${inner}</s>`
    case 'code':
      return `<code>${inner}</code>`
    case 'pre':
      return `<pre>${inner}</pre>`
    case 'blockquote':
      return `<blockquote>${inner}</blockquote>`
    case 'tg-spoiler':
      return `<span class="tg-spoiler">${inner}</span>`
    case 'a': {
      const href = el.getAttribute('href') ?? ''
      return /^(https?:|tg:)/i.test(href)
        ? `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer noopener">${inner}</a>`
        : inner
    }
    default:
      return inner // tg-emoji и всё неизвестное — просто содержимое
  }
}

/** Шаблон бота → безопасный HTML для превью: значки и подстановки заменены. */
export function telegramHtml(
  template: string,
  vars: Record<string, string>,
  icons: Record<string, string>,
): string {
  const text = template
    .replace(/\{@([a-z_]+)\}/g, (_, name: string) => icons[name] ?? '')
    .replace(/\{([a-z_]+)\}/g, (match, name: string) =>
      name in vars ? escapeHtml(vars[name]) : match,
    )
  const doc = new DOMParser().parseFromString(`<body>${text}</body>`, 'text/html')
  return Array.from(doc.body.childNodes).map(serialize).join('')
}

export type PreviewStyle = 'none' | 'primary' | 'success' | 'danger'

const BUTTON_STYLE: Record<PreviewStyle, string> = {
  none: 'bg-white/10 text-white',
  primary: 'bg-sky-500/90 text-white',
  success: 'bg-emerald-500/90 text-white',
  danger: 'bg-rose-500/90 text-white',
}

export interface PreviewButton {
  text: string
  style?: PreviewStyle
}

export function TelegramPreview({
  name,
  html,
  buttons = [],
  columns = 1,
  className,
}: {
  name?: string
  html: string
  buttons?: PreviewButton[]
  columns?: number
  className?: string
}) {
  const safe = useMemo(() => ({ __html: html }), [html])
  return (
    <div className={cn('rounded-3xl bg-[#0e1621] p-3 shadow-inner', className)}>
      {name && <p className="mb-2 px-1 text-xs font-medium text-white/60">{name}</p>}
      <div
        className="tg-bubble rounded-2xl rounded-bl-md bg-[#182533] px-3 py-2 text-[13px] leading-snug text-white/90"
        dangerouslySetInnerHTML={safe}
      />
      {buttons.length > 0 && (
        <div
          className="mt-1 grid gap-1"
          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        >
          {buttons.map((button, index) => (
            <span
              key={`${button.text}-${index}`}
              className={cn(
                'truncate rounded-lg px-2 py-1.5 text-center text-[11px]',
                BUTTON_STYLE[button.style ?? 'none'],
              )}
            >
              {button.text}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
