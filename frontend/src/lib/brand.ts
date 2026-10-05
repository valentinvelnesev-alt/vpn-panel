import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { panel } from '@/lib/api'

export const DEFAULT_TITLE = 'Panel'

/**
 * Название и логотип панели из «Настройки → Название панели».
 * Ставит <title> вкладки и favicon — в том числе на странице входа, поэтому
 * берётся из публичного эндпоинта, а не из настроек под авторизацией.
 */
export function useBrand() {
  const { data } = useQuery({
    queryKey: ['brand-public'],
    queryFn: panel.publicBrand,
    staleTime: Infinity,
    retry: false,
  })
  const title = data?.title || DEFAULT_TITLE
  const logo = data?.logo_url || null

  useEffect(() => {
    document.title = title
  }, [title])

  useEffect(() => {
    if (!logo) return
    let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      document.head.appendChild(link)
    }
    link.href = logo
  }, [logo])

  return { title, logo }
}
