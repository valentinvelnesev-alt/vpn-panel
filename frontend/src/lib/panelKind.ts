import { useQuery } from '@tanstack/react-query'
import { panel } from '@/lib/api'

// Тип VPN-панели выбирается при установке (PANEL_TYPE в .env). От него
// зависят только подписи: у 3x-ui вместо сквадов — инбаунды.
export interface PanelTerms {
  is3xui: boolean
  name: string
  squads: string
  squadsOf: string
  noSquad: string
}

const REMNAWAVE: PanelTerms = {
  is3xui: false,
  name: 'Remnawave',
  squads: 'Сквады',
  squadsOf: 'Сквады Remnawave',
  noSquad: 'Не выбран ни один сквад',
}

const XUI: PanelTerms = {
  is3xui: true,
  name: '3x-ui',
  squads: 'Инбаунды',
  squadsOf: 'Инбаунды 3x-ui',
  noSquad: 'Не выбран ни один инбаунд',
}

export function usePanelTerms(): PanelTerms {
  const { data } = useQuery({
    queryKey: ['settings', 'remnawave'],
    queryFn: panel.remnawaveSettings,
    staleTime: 5 * 60_000,
  })
  return data?.panel_type === '3xui' ? XUI : REMNAWAVE
}
