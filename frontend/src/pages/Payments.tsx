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
  rollypay: 'СБП (резерв)',
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

function RollyPayCard({ providers }: { providers: Providers }) {
  const queryClient = useQueryClient()
  const [enabled, setEnabled] = useState(providers.rollypay_enabled)
  const [apiKey, setApiKey] = useState('')
  const [signingSecret, setSigningSecret] = useState('')

  const save = useMutation({
    mutationFn: () =>
      panel.saveRollyPay({ enabled, api_key: apiKey, signing_secret: signingSecret }),
    onSuccess: (data) => {
      queryClient.setQueryData(['providers'], data)
      setApiKey('')
      setSigningSecret('')
    },
  })

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="font-medium">СБП, резервный способ (RollyPay)</h2>
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
          label="API-ключ кассы"
          hint={
            providers.rollypay_api_key_masked
              ? `Сохранён: ${providers.rollypay_api_key_masked}. Пусто — не менять.`
              : undefined
          }
        >
          <Input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="off"
          />
        </Field>
        <Field
          label="Секрет подписи вебхуков (необязательно)"
          hint={
            providers.rollypay_signing_secret_masked
              ? `Сохранён: ${providers.rollypay_signing_secret_masked}. Пусто — не менять. С ним колбэки без валидной подписи отбрасываются.`
              : 'Из кабинета RollyPay. С ним колбэки без валидной подписи отбрасываются; без него оплата всё равно подтверждается перезапросом статуса.'
          }
        >
          <Input
            type="password"
            value={signingSecret}
            onChange={(e) => setSigningSecret(e.target.value)}
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

function WebhooksCard({ providers }: { providers: Providers }) {
  const urls = providers.webhook_urls ?? {}
  const rows: [string, string][] = [
    ['Platega', urls.platega],
    ['RollyPay', urls.rollypay],
    ['CryptoBot', urls.cryptobot],
  ].filter((r): r is [string, string] => Boolean(r[1]))

  return (
    <Card>
      <h2 className="font-medium">Адреса для уведомлений об оплате</h2>
      <p className="mt-1 text-sm text-muted">
        Провайдеры не получают адрес колбэка при создании платежа — его нужно один раз
        вписать в личном кабинете провайдера. Без этого оплата подтверждается только
        опросом статуса (кнопка «Проверить оплату» и фоновая проверка каждые 30 секунд).
      </p>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-danger">
          Не задан публичный адрес панели (PANEL_PUBLIC_URL в .env) — вебхуки принимать
          некуда.
        </p>
      ) : (
        <dl className="mt-3 space-y-2 text-sm">
          {rows.map(([name, url]) => (
            <div key={name} className="flex flex-wrap items-baseline gap-x-3">
              <dt className="w-24 shrink-0 text-muted">{name}</dt>
              <dd>
                <code className="select-all break-all rounded bg-bg px-2 py-1">{url}</code>
              </dd>
            </div>
          ))}
        </dl>
      )}
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
      <RollyPayCard providers={providers} />
      <CryptoBotCard providers={providers} />
      <StarsCard providers={providers} />
      <WebhooksCard providers={providers} />
      <PaymentsLog />
    </div>
  )
}
