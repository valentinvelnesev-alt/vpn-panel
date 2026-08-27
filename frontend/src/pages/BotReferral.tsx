import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type ReferralSettings } from '@/lib/api'
import { number } from '@/lib/format'

export default function BotReferral() {
  const queryClient = useQueryClient()
  const { data: settings } = useQuery({
    queryKey: ['referral-settings'],
    queryFn: panel.referralSettings,
  })
  const { data: stats } = useQuery({
    queryKey: ['referral-stats'],
    queryFn: panel.referralStats,
  })

  const [form, setForm] = useState<ReferralSettings | null>(null)
  useEffect(() => {
    if (settings) setForm(settings)
  }, [settings])

  const save = useMutation({
    mutationFn: () => panel.saveReferralSettings(form!),
    onSuccess: (data) => queryClient.setQueryData(['referral-settings'], data),
  })

  if (!form) return null

  const set = <K extends keyof ReferralSettings>(key: K, value: ReferralSettings[K]) =>
    setForm((f) => ({ ...f!, [key]: value }))

  return (
    <Card>
      <h2 className="font-medium">Реферальная программа</h2>
      <p className="mt-1 text-sm text-muted">
        Приглашённый получает бонусные дни к триалу. Пригласивший — дни, когда
        приглашённый впервые оплатит подписку (не за саму регистрацию).
      </p>

      <form
        className="mt-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={form.referral_enabled}
            onChange={(e) => set('referral_enabled', e.target.checked)}
          />
          Включена
        </label>

        {form.referral_enabled && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Дней пригласившему за оплату друга">
              <Input
                type="number"
                min={0}
                value={form.referral_reward_days}
                onChange={(e) => set('referral_reward_days', Number(e.target.value))}
              />
            </Field>
            <Field label="Бонусных дней приглашённому">
              <Input
                type="number"
                min={0}
                value={form.referral_bonus_days}
                onChange={(e) => set('referral_bonus_days', Number(e.target.value))}
              />
            </Field>
          </div>
        )}

        <div className="border-t pt-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={form.referral_commission_enabled}
              onChange={(e) => set('referral_commission_enabled', e.target.checked)}
            />
            Денежная комиссия на баланс (с каждой оплаты, 2 уровня)
          </label>
          <p className="mt-1 text-sm text-muted">
            Работает независимо от бонуса в днях выше: с каждой оплаты
            приглашённого на баланс пригласившего зачисляется процент, и ещё
            меньший процент — тому, кто пригласил самого пригласившего.
          </p>

          {form.referral_commission_enabled && (
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="Процент 1 уровня (прямой реферал)">
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={form.referral_level1_percent}
                  onChange={(e) => set('referral_level1_percent', Number(e.target.value))}
                />
              </Field>
              <Field label="Процент 2 уровня (реферал реферала)">
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={form.referral_level2_percent}
                  onChange={(e) => set('referral_level2_percent', Number(e.target.value))}
                />
              </Field>
            </div>
          )}
        </div>

        {save.isSuccess && <p className="text-sm text-success">Сохранено</p>}
        <Button type="submit" disabled={save.isPending}>
          Сохранить
        </Button>
      </form>

      {stats && (
        <div className="mt-6 border-t pt-4">
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-xs text-muted">Приглашено всего</dt>
              <dd className="text-xl font-semibold tabular-nums">
                {number(stats.total_referred)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Начислено дней</dt>
              <dd className="text-xl font-semibold tabular-nums">
                {number(stats.total_rewards_days)}
              </dd>
            </div>
          </dl>

          {stats.top.length > 0 && (
            <ul className="mt-4 space-y-1 text-sm">
              {stats.top.map((row) => (
                <li key={row.telegram_id} className="flex justify-between">
                  <span>{row.username ? `@${row.username}` : `TG ${row.telegram_id}`}</span>
                  <span className="text-muted">
                    {row.invited} приглашённых · {row.rewarded_days} дн.
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
