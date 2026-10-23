import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { panel, type BotSettings, type BotStatus } from '@/lib/api'
import { useApplyConfirmation } from '@/lib/applied'

/** Состояние бота. Пока команда запуска ещё не дошла (включён, но
 *  «остановлен») — опрашиваем чаще, чтобы сразу увидеть «работает». */
export function useBotStatus() {
  return useQuery({
    queryKey: ['bot'],
    queryFn: panel.botStatus,
    refetchInterval: (query) => {
      const data = query.state.data
      const starting = !!data && data.enabled && data.state === 'stopped'
      return starting ? 1500 : 15_000
    },
  })
}

const SETTINGS_KEYS: (keyof BotSettings)[] = [
  'welcome_text',
  'support_url',
  'channel_url',
  'channel_id',
  'require_channel_sub',
  'trial_enabled',
  'trial_days',
  'trial_squad_uuids',
  'trial_hwid_limit',
  'purchase_notify_chat_id',
  'admin_telegram_ids',
  'privacy_policy_url',
  'terms_url',
  'allow_multiple_subscriptions',
  'menu_hidden',
  'menu_columns',
  'menu_styles',
  'menu_labels',
  'menu_mode',
]

function settingsOf(status: BotStatus): BotSettings {
  return Object.fromEntries(SETTINGS_KEYS.map((k) => [k, status[k]])) as unknown as BotSettings
}

/**
 * Черновик настроек бота для одной подстраницы. Настройки сохраняются одним
 * запросом целиком, поэтому черновик стартует со всех текущих значений, а
 * страница меняет только свои поля — чужие уходят как были.
 */
export function useBotSettingsDraft(status: BotStatus) {
  const queryClient = useQueryClient()
  const saved = useMemo(() => settingsOf(status), [status])
  const [form, setForm] = useState<BotSettings>(saved)
  const [savedAt, setSavedAt] = useState<number>()
  const { applied, begin } = useApplyConfirmation()

  useEffect(() => setForm(saved), [saved])

  const dirty = JSON.stringify(form) !== JSON.stringify(saved)

  const save = useMutation({
    mutationFn: (override?: Partial<BotSettings>) =>
      panel.saveBotSettings({ ...form, ...override }),
    onSuccess: (data) => {
      queryClient.setQueryData(['bot'], data)
      setSavedAt(Date.now())
      begin()
    },
  })

  const set = <K extends keyof BotSettings>(key: K, value: BotSettings[K]) => {
    save.reset()
    setForm((f) => ({ ...f, [key]: value }))
  }

  return {
    form,
    set,
    dirty,
    savedAt,
    applied,
    save,
    reset: () => {
      save.reset()
      setForm(saved)
    },
    error: save.isError ? (save.error as Error).message : null,
  }
}
