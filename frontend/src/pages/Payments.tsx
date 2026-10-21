import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PageHeader, Toggle } from '@/components/page'
import { useEffect, useState } from 'react'
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
  cispay: 'cisPay',
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
        <Toggle
          checked={enabled}
          onChange={(value) => setEnabled(value)}
          label="Включено"
        />
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
        <Toggle
          checked={enabled}
          onChange={(value) => setEnabled(value)}
          label="Включено"
        />
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

function CisPayCard({ providers }: { providers: Providers }) {
  const queryClient = useQueryClient()
  const [enabled, setEnabled] = useState(providers.cispay_enabled)
  const [shopId, setShopId] = useState(providers.cispay_shop_id ?? '')
  const [apiKey, setApiKey] = useState('')
  const body = () => ({ enabled, shop_id: shopId.trim(), api_key: apiKey.trim() })

  const check = useMutation({ mutationFn: () => panel.checkCisPay(body()) })
  const save = useMutation({
    mutationFn: () => panel.saveCisPay(body()),
    onSuccess: (data) => {
      queryClient.setQueryData(['providers'], data)
      setApiKey('')
    },
  })

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium">cisPay — СБП, карта, криптовалюта</h2>
          <p className="mt-1 text-sm text-muted">
            Клиент нажимает одну кнопку и сам выбирает способ на странице cisPay — из тех, что
            подключены у вашего магазина. Ключи — в кабинете cisPay, раздел «Настройки магазина».
            Если панель на домене с HTTPS, там же укажите адрес для вебхука (внизу этой
            страницы).
          </p>
        </div>
        <Toggle checked={enabled} onChange={setEnabled} label="Включено" />
      </div>
      <form
        className="mt-4 space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Shop ID">
            <Input
              value={shopId}
              onChange={(e) => {
                setShopId(e.target.value)
                check.reset()
              }}
              placeholder="0190…-…"
              autoComplete="off"
            />
          </Field>
          <Field
            label="API-ключ (cis_sec_…)"
            hint={
              providers.cispay_api_key_masked
                ? `Сохранён: ${providers.cispay_api_key_masked}. Пусто — не менять.`
                : 'Им же cisPay подписывает вебхуки — подпись проверяется.'
            }
          >
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value)
                check.reset()
              }}
              autoComplete="off"
            />
          </Field>
        </div>
        {check.data && (
          <p className={`text-sm ${check.data.ok ? 'text-success' : 'text-danger'}`}>
            {check.data.message}
          </p>
        )}
        {save.isError && <p className="text-sm text-danger">{(save.error as Error).message}</p>}
        {save.isSuccess && <p className="text-sm text-success">Сохранено</p>}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={save.isPending}>
            Сохранить
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={check.isPending}
            onClick={() => check.mutate()}
          >
            {check.isPending ? 'Проверяю…' : 'Проверить подключение'}
          </Button>
        </div>
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
        <Toggle
          checked={enabled}
          onChange={(value) => setEnabled(value)}
          label="Включено"
        />
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
  const [rate, setRate] = useState(String(providers.stars_rub_per_star))
  useEffect(() => setRate(String(providers.stars_rub_per_star)), [providers.stars_rub_per_star])
  const save = useMutation({
    mutationFn: (json: { enabled: boolean; rub_per_star?: number }) => panel.saveStars(json),
    onSuccess: (data) => queryClient.setQueryData(['providers'], data),
  })
  const rateValue = Number(rate.replace(',', '.'))
  const rateValid = rateValue >= 0.1 && rateValue <= 100

  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-medium">Telegram Stars</h2>
          <p className="mt-1 text-sm text-muted">
            Не требует настройки. Официального курса к рублю нет — задайте свой ниже.
          </p>
        </div>
        <Toggle
          checked={providers.stars_enabled}
          onChange={(value) => save.mutate({ enabled: value })}
          label="Включено"
        />
      </div>
      <form
        className="mt-4 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({ enabled: providers.stars_enabled, rub_per_star: rateValue })
        }}
      >
        <Field
          label="Курс: 1 звезда = ? ₽"
          hint={
            rateValid
              ? `Тариф за 199 ₽ будет стоить ${Math.max(1, Math.round(199 / rateValue))} ⭐`
              : 'От 0.1 до 100'
          }
        >
          <Input
            inputMode="decimal"
            className="max-w-40"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
          />
        </Field>
        <Button
          type="submit"
          variant="ghost"
          disabled={!rateValid || rateValue === providers.stars_rub_per_star || save.isPending}
        >
          Сохранить курс
        </Button>
      </form>
      {save.isError && <p className="mt-2 text-sm text-danger">{(save.error as Error).message}</p>}
    </Card>
  )
}

function WebhooksCard({ providers }: { providers: Providers }) {
  const urls = providers.webhook_urls ?? {}
  const rows: [string, string][] = [
    ['Platega', urls.platega],
    ['RollyPay', urls.rollypay],
    ['cisPay', urls.cispay],
    ['CryptoBot', urls.cryptobot],
  ].filter((r): r is [string, string] => Boolean(r[1]))

  return (
    <Card>
      <h2 className="font-medium">Адреса для уведомлений об оплате</h2>
      {rows.length === 0 ? (
        <p className="mt-1 text-sm text-muted">
          Панель открыта по IP, без домена и HTTPS — платёжки на такой адрес уведомления не
          шлют, поэтому адресов здесь нет. Это не страшно: бот сам проверяет статус оплаты
          каждые 30 секунд, а клиент может нажать «Проверить оплату» — доступ выдаётся
          автоматически. Подключите домен, если хотите мгновенное подтверждение.
        </p>
      ) : (
        <>
        <p className="mt-1 text-sm text-muted">
          Впишите адрес один раз в личном кабинете провайдера — тогда оплата подтверждается
          мгновенно. Без него бот всё равно проверяет статус каждые 30 секунд.
        </p>
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
        </>
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
      <PageHeader
        title="Платежи"
        description="Способы оплаты в боте и журнал всех оплат. Включённый способ сразу появляется у клиентов."
      />
      <PlategaCard providers={providers} />
      <RollyPayCard providers={providers} />
      <CisPayCard providers={providers} />
      <CryptoBotCard providers={providers} />
      <StarsCard providers={providers} />
      <WebhooksCard providers={providers} />
      <PaymentsLog />
    </div>
  )
}
