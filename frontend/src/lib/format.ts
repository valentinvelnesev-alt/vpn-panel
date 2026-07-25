const UNITS = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ', 'ПБ']

export function bytes(value: number): string {
  if (!value) return '0 Б'
  const power = Math.min(Math.floor(Math.log(value) / Math.log(1024)), UNITS.length - 1)
  const size = value / 1024 ** power
  return `${size.toFixed(power === 0 ? 0 : 1)} ${UNITS[power]}`
}

export function number(value: number): string {
  return value.toLocaleString('ru-RU')
}

export function date(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

export function dateTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** «через 12 дней» / «истекла 3 дня назад» — так понятнее, чем голая дата. */
export function untilExpiry(value: string | null): { text: string; expired: boolean } {
  if (!value) return { text: 'бессрочно', expired: false }
  const days = Math.round((new Date(value).getTime() - Date.now()) / 86_400_000)
  if (days < 0) {
    return { text: `истекла ${plural(-days, 'день', 'дня', 'дней')} назад`, expired: true }
  }
  if (days === 0) return { text: 'истекает сегодня', expired: false }
  return { text: `${plural(days, 'день', 'дня', 'дней')}`, expired: false }
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return `${n} ${one}`
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} ${few}`
  return `${n} ${many}`
}

export function uptime(seconds: number): string {
  if (!seconds) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  if (days) return `${days} д ${hours} ч`
  const minutes = Math.floor((seconds % 3600) / 60)
  return hours ? `${hours} ч ${minutes} мин` : `${minutes} мин`
}
