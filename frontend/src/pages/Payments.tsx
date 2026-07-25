import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type Providers } from '@/lib/api'
import { dateTime, number } from '@/lib/format'

const STATUS_LABEL: Record<string, string> = {
  pending: 'ожидает',
  paid: 'оплачен',
  failed: 'ошибка',
  expired: 'истёк',
}

const PROVIDER_LABEL: Record<string, string> = {
  platega: 'СБП / карта',
  cryptobot: 'Криптовалюта',
  stars: 'Telegram Stars',
}

function PlategaCard({ providers }: { providers: Providers }) {
  const queryClient = useQueryClient()
  const [enabled, setEnabled] = useState(providers.platega_enabled)
  const [merchantId, setMerchantId] = useState(providers.platega_merchant_id ?? '')
  const [secret, setSecret] = useState('')

  const save = useMutation({
    mutationFn: () => panel.savePlatega({ enabled, merchant_id: merchantId, secret }),
    onSuccess: (data) => {
      queryClient.setQueryData(['providers'], data)
      setSecret('')
    },
  })

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="font-medium">СБП / карта (Platega)</h2>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Включено
        </label>
      </div>
      <form
        className="mt-4 space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <Field label="Merchant ID">
          <Input value={merchantId} onChange={(e) => setMerchantId(e.target.value)} />
        </Field>
        <Field
          label="Секрет"
          hint={
            providers.platega_secret_masked
              ? `Сохранён: ${providers.platega_secret_masked}. Пусто — не менять.`
              : undefined
          }
        >
          <Input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="off"
          />
        </Field>
        <Button type="submit" disabled={save.isPending}>
          Сохранить
        </Button>
      </form>
    </Card>
  )
}

function CryptoBotCard({ providers }: { providers: Providers }) {
  const queryClient = useQueryClient()
  const [enabled, setEnabled] = useState(providers.cryptobot_enabled)
  const [token, setToken] = useState('')

  const save = useMutation({
    mutationFn: () => panel.saveCryptoBot({ enabled, token }),
    onSuccess: (data) => {
      queryClient.setQueryData(['providers'], data)
      setToken('')
    },
  })

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="font-medium">Криптовалюта (@CryptoBot)</h2>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Включено
        </label>
      </div>
      <form
        className="mt-4 space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <Field
          label="Токен приложения"
          hint={
            providers.cryptobot_token_masked
              ? `Сохранён: ${providers.cryptobot_token_masked}. Пусто — не менять.`
              : 'Создаётся в @CryptoBot → Crypto Pay → My Apps.'
          }
        >
          <Input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="off"
          />
        </Field>
        <Button type="submit" disabled={save.isPending}>
          Сохранить
        </Button>
      </form>
    </Card>
  )
}

function StarsCard({ providers }: { providers: Providers }) {
  const queryClient = useQueryClient()
  const save = useMutation({
    mutationFn: (enabled: boolean) => panel.saveStars({ enabled }),
    onSuccess: (data) => queryClient.setQueryData(['providers'], data),
  })

  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-medium">Telegram Stars</h2>
          <p className="mt-1 text-sm text-muted">
            Не требует настройки — курс к рублю приблизительный.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={providers.stars_enabled}
            onChange={(e) => save.mutate(e.target.checked)}
          />
          Включено
        </label>
      </div>
    </Card>
  )
}

function PaymentsLog() {
  const { data } = useQuery({ queryKey: ['payments'], queryFn: () => panel.payments(50) })

  if (!data) return null

  return (
    <Card>
      <h2 className="font-medium">Последние платежи</h2>
      {data.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Пока пусто.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="pb-2 pr-4">Пользователь</th>
                <th className="pb-2 pr-4">Способ</th>
                <th className="pb-2 pr-4">Сумма</th>
                <th className="pb-2 pr-4">Статус</th>
                <th className="pb-2">Создан</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.map((p) => (
                <tr key={p.id}>
                  <td className="py-2 pr-4">
                    {p.username ? `@${p.username}` : `TG ${p.telegram_id}`}
                  </td>
                  <td className="py-2 pr-4">{PROVIDER_LABEL[p.provider] ?? p.provider}</td>
                  <td className="py-2 pr-4 tabular-nums">{number(p.amount_rub)} ₽</td>
                  <td className="py-2 pr-4">{STATUS_LABEL[p.status] ?? p.status}</td>
                  <td className="py-2 text-muted">{dateTime(p.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

export default function Payments() {
  const { data: providers } = useQuery({ queryKey: ['providers'], queryFn: panel.providers })

  if (!providers) return <p className="text-sm text-muted">Загрузка…</p>

  return (
    <div className="max-w-3xl space-y-6">
      <PlategaCard providers={providers} />
      <CryptoBotCard providers={providers} />
      <StarsCard providers={providers} />
      <PaymentsLog />
    </div>
  )
}
