import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'
import { panel, type BotStatus } from '@/lib/api'

export interface Applied {
  state: 'idle' | 'waiting' | 'applied' | 'stale'
  at: string | null
}

const IDLE: Applied = { state: 'idle', at: null }
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * Подтверждение, что бот действительно получил настройки. Панель сохраняет
 * в базу и просит бота перечитать её, но сообщение могло потеряться — раньше
 * мы всё равно писали «применено». Теперь ждём отметку «бот применил» (её
 * ставит сам бот) и честно говорим, если она не появилась. begin() зовут
 * сразу после успешного сохранения.
 */
export function useApplyConfirmation() {
  const queryClient = useQueryClient()
  const [applied, setApplied] = useState<Applied>(IDLE)
  const run = useRef(0)

  const begin = useCallback(() => {
    const baseline = queryClient.getQueryData<BotStatus>(['bot'])?.config_applied_at ?? null
    const id = ++run.current
    setApplied({ state: 'waiting', at: null })
    void (async () => {
      for (let attempt = 0; attempt < 14; attempt++) {
        await sleep(1000)
        if (run.current !== id) return
        try {
          const status = await panel.botStatus()
          queryClient.setQueryData(['bot'], status)
          if (status.config_applied_at && status.config_applied_at !== baseline) {
            setApplied({ state: 'applied', at: status.config_applied_at })
            return
          }
        } catch {
          // Панель на секунду недоступна — попробуем ещё раз.
        }
      }
      if (run.current === id) setApplied({ state: 'stale', at: null })
    })()
  }, [queryClient])

  const reset = useCallback(() => {
    run.current++
    setApplied(IDLE)
  }, [])

  return { applied, begin, reset }
}
