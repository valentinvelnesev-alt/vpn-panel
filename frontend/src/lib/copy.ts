import { useQuery } from '@tanstack/react-query'
import { panel, type BotCopy, type CopyItem } from '@/lib/api'

/** Стандартные подписи, тексты и подстановки бота — единый реестр с сервера. */
export function useBotCopy() {
  return useQuery({ queryKey: ['bot-copy'], queryFn: panel.botCopy, staleTime: 60_000 })
}

export const byKey = (items: CopyItem[] | undefined) =>
  Object.fromEntries((items ?? []).map((item) => [item.key, item])) as Record<string, CopyItem>

/** Значения подстановок для превью: пример из реестра. */
export function examples(item: CopyItem | undefined): Record<string, string> {
  return Object.fromEntries((item?.variables ?? []).map((v) => [v.name, v.example]))
}

export type { BotCopy }
