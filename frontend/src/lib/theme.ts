/**
 * Цвет акцента панели: кнопки, активный пункт меню, переключатели, частицы
 * фона. Набор готовых цветов, а не свободный выбор — любой из них читается
 * на тёмном фоне и не ломает контраст текста на кнопках.
 */

export const ACCENTS = {
  blue: { label: 'Синий', value: 'oklch(0.68 0.16 250)' },
  cyan: { label: 'Бирюзовый', value: 'oklch(0.76 0.12 205)' },
  violet: { label: 'Фиолетовый', value: 'oklch(0.68 0.19 295)' },
  green: { label: 'Зелёный', value: 'oklch(0.74 0.16 155)' },
  orange: { label: 'Оранжевый', value: 'oklch(0.76 0.15 60)' },
  pink: { label: 'Розовый', value: 'oklch(0.72 0.18 350)' },
} as const

export type Accent = keyof typeof ACCENTS
export const DEFAULT_ACCENT: Accent = 'blue'

export function isAccent(value: unknown): value is Accent {
  return typeof value === 'string' && value in ACCENTS
}

export function applyAccent(accent: string | null | undefined) {
  const key = isAccent(accent) ? accent : DEFAULT_ACCENT
  document.documentElement.style.setProperty('--color-accent', ACCENTS[key].value)
}
