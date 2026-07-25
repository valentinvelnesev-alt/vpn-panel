/**
 * Код страны (ISO 3166-1 alpha-2) → эмодзи-флаг.
 *
 * Флаги в Unicode собираются из двух regional indicator symbols: буква A
 * соответствует U+1F1E6, и так далее. Поэтому таблица на 250 стран не нужна —
 * достаточно сдвига кодовой точки.
 */
export function countryFlag(code: string | null | undefined): string | null {
  if (!code) return null
  const normalized = code.trim().toUpperCase()
  if (!/^[A-Z]{2}$/.test(normalized)) return null

  const OFFSET = 0x1f1e6 - 'A'.charCodeAt(0)
  return String.fromCodePoint(
    ...[...normalized].map((char) => char.charCodeAt(0) + OFFSET),
  )
}
