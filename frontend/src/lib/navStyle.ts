import { useEffect, useState } from 'react'

/**
 * Стиль навигации: боковая панель или компактная верхняя полоса.
 * Это личное предпочтение администратора на конкретном устройстве, поэтому
 * хранится в localStorage, а не в БД — так у каждого свой выбор без
 * лишнего похода в API на каждой загрузке.
 */

export type NavStyle = 'sidebar' | 'compact'

const STORAGE_KEY = 'panel:nav-style'
const EVENT = 'panel:nav-style-changed'

export function getNavStyle(): NavStyle {
  return localStorage.getItem(STORAGE_KEY) === 'compact' ? 'compact' : 'sidebar'
}

export function setNavStyle(style: NavStyle) {
  localStorage.setItem(STORAGE_KEY, style)
  // Своё событие: `storage` срабатывает только в других вкладках, а нам
  // нужно обновить текущую.
  window.dispatchEvent(new CustomEvent(EVENT))
}

export function useNavStyle(): NavStyle {
  const [style, setStyle] = useState<NavStyle>(getNavStyle)

  useEffect(() => {
    const sync = () => setStyle(getNavStyle())
    window.addEventListener(EVENT, sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener(EVENT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [])

  return style
}
