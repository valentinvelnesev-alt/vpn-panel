import { useEffect, useId } from 'react'

/**
 * Несохранённые изменения на странице. Раньше правки молча терялись: кнопка
 * «Сохранить» была в самом низу длинной формы, её не замечали и уходили на
 * другую страницу. Теперь меню переспрашивает перед уходом, а браузер — перед
 * закрытием вкладки.
 */

const dirty = new Set<string>()

export function hasUnsaved(): boolean {
  return dirty.size > 0
}

/** true — можно уходить (изменений нет или пользователь согласился их бросить). */
export function confirmLeave(): boolean {
  if (!hasUnsaved()) return true
  const ok = window.confirm('Есть несохранённые изменения. Уйти без сохранения?')
  if (ok) dirty.clear()
  return ok
}

export function useUnsavedGuard(isDirty: boolean) {
  const id = useId()
  useEffect(() => {
    if (isDirty) dirty.add(id)
    else dirty.delete(id)
    return () => {
      dirty.delete(id)
    }
  }, [id, isDirty])

  useEffect(() => {
    if (!isDirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [isDirty])
}
