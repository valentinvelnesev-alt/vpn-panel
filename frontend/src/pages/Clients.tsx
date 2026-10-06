import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Contact, Plus, Search, Star, Trash2, Wallet } from 'lucide-react'
import { useState } from 'react'
import { Modal } from '@/components/Modal'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type ClientDetail, type ClientRow, type PlanInput } from '@/lib/api'
import { dateTime, number, untilExpiry } from '@/lib/format'
import { EMPTY_PLAN, PlanForm } from './BotPlans'
import { usePanelTerms } from '@/lib/panelKind'

const PAGE_SIZE = 50

const TX_LABEL: Record<string, string> = {
  topup: 'Пополнение',
  purchase: 'Оплата тарифа',
  refund: 'Возврат',
  referral_reward: 'Реферальная комиссия',
  admin_adjust: 'Администратор',
  auto_renewal: 'Автопродление',
}

const SOURCE_LABEL: Record<string, string> = {
  trial: 'пробный период',
  renewal: 'продление',
  auto_renewal: 'автопродление',
  wallet: 'с баланса',
  stars: 'Telegram Stars',
  platega: 'СБП / карта',
  rollypay: 'СБП',
  cryptobot: 'криптовалюта',
  promo: 'промокод',
  referral_reward: 'бонус за друга',
}

const rub = (value: number) =>
  `${value.toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} ₽`

function clientName(c: ClientRow) {
  return c.username ? `@${c.username}` : c.first_name || String(c.telegram_id)
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-border/60 p-4">
      <h3 className="border-b border-border/60 pb-3 text-sm font-medium">{title}</h3>
      <div className="mt-4 space-y-3">{children}</div>
    </section>
  )
}

function BalanceForm({ client }: { client: ClientDetail }) {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<'credit' | 'debit'>('credit')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [notify, setNotify] = useState(true)

  const adjust = useMutation({
    mutationFn: () => {
      const value = Number(amount.replace(',', '.'))
      if (!(value > 0)) throw new Error('Укажите сумму больше нуля')
      return panel.adjustBalance(client.id, {
        amount_rub: mode === 'credit' ? value : -value,
        reason: reason.trim(),
        notify,
      })
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['client', client.id], data)
      queryClient.invalidateQueries({ queryKey: ['clients'] })
      setAmount('')
      setReason('')
    },
  })

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        adjust.mutate()
      }}
    >
      <div className="flex gap-2">
        {(['credit', 'debit'] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={`flex-1 rounded-full border px-3 py-2 text-sm transition-colors ${
              mode === m
                ? 'border-accent/60 bg-accent/10 font-medium text-accent'
                : 'border-border/60 text-muted hover:bg-surface-hover'
            }`}
          >
            {m === 'credit' ? 'Начислить' : 'Списать'}
          </button>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-[1fr_2fr]">
        <Field label="Сумма, ₽">
          <Input
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="100"
            required
          />
        </Field>
        <Field label="Причина" hint="Видна в истории операций и клиенту в уведомлении">
          <Input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Компенсация за простой"
            maxLength={200}
            required
          />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4"
          checked={notify}
          onChange={(e) => setNotify(e.target.checked)}
        />
        Уведомить клиента в боте
      </label>
      {adjust.isError && <p className="text-sm text-danger">{(adjust.error as Error).message}</p>}
      <Button type="submit" disabled={adjust.isPending || !amount || !reason.trim()}>
        {mode === 'credit' ? 'Начислить' : 'Списать'}
      </Button>
    </form>
  )
}

function PersonalPlans({ client }: { client: ClientDetail }) {
  const terms = usePanelTerms()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['client', client.id] })
    setEditing(null)
  }

  const create = useMutation({
    mutationFn: (plan: PlanInput) => panel.createPersonalPlan(client.id, plan),
    onSuccess: refresh,
  })
  const update = useMutation({
    mutationFn: ({ id, plan }: { id: number; plan: PlanInput }) =>
      panel.updatePersonalPlan(client.id, id, plan),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (id: number) => panel.deletePersonalPlan(client.id, id),
    onSuccess: refresh,
  })

  return (
    <>
      <p className="text-xs text-muted">
        Видны в боте только этому клиенту — сверху списка тарифов, со звёздочкой. Своя цена,
        срок, {terms.squads.toLowerCase()} и лимит устройств. Общая скидка на них не действует.
      </p>
      {client.personal_plans.map((plan) =>
        editing === plan.id ? (
          <PlanForm
            key={plan.id}
            personal
            initial={plan}
            pending={update.isPending}
            error={update.isError ? (update.error as Error).message : null}
            onSubmit={(data) => update.mutate({ id: plan.id, plan: data })}
            onCancel={() => setEditing(null)}
          />
        ) : (
          <div
            key={plan.id}
            className="flex items-center justify-between gap-3 rounded-2xl border px-3 py-2"
          >
            <button
              type="button"
              className="min-w-0 flex-1 text-left text-sm"
              onClick={() => setEditing(plan.id)}
            >
              <Star className="mr-1 inline size-3.5 text-warning" />
              <span className="font-medium">{plan.title}</span>
              <span className="text-muted">
                {' '}
                · {plan.days} дн. · {rub(plan.price_rub)} · {plan.hwid_limit} устр.
                {!plan.is_active && ' · скрыт'}
              </span>
            </button>
            <button
              type="button"
              onClick={() => remove.mutate(plan.id)}
              className="text-muted hover:text-danger"
              aria-label="Удалить тариф"
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        ),
      )}
      {editing === 'new' ? (
        <PlanForm
          personal
          initial={{ ...EMPTY_PLAN, title: 'Персональный' }}
          pending={create.isPending}
          error={create.isError ? (create.error as Error).message : null}
          onSubmit={(plan) => create.mutate(plan)}
          onCancel={() => setEditing(null)}
        />
      ) : (
        <Button type="button" variant="ghost" onClick={() => setEditing('new')}>
          <Plus className="size-4" />
          Добавить персональный тариф
        </Button>
      )}
    </>
  )
}

function DeleteClient({ client, onDeleted }: { client: ClientDetail; onDeleted: () => void }) {
  const terms = usePanelTerms()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [withKeys, setWithKeys] = useState(true)
  const remove = useMutation({
    mutationFn: () => panel.deleteClient(client.id, withKeys),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['clients'] })
      onDeleted()
    },
  })

  if (!open) {
    return (
      <Button variant="ghost" className="text-danger" onClick={() => setOpen(true)}>
        <Trash2 className="size-4" />
        Удалить клиента
      </Button>
    )
  }
  return (
    <div className="space-y-3 rounded-2xl border border-danger/40 p-4">
      <p className="text-sm">
        Будут удалены баланс, история, персональные тарифы и ключи клиента в боте. Если он
        снова напишет боту — появится как новый.
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4"
          checked={withKeys}
          onChange={(e) => setWithKeys(e.target.checked)}
        />
        Удалить и его ключи в {terms.name} ({client.subscriptions_list.length}) — VPN перестанет
        работать
      </label>
      {remove.isError && <p className="text-sm text-danger">{(remove.error as Error).message}</p>}
      <div className="flex gap-2">
        <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
          {remove.isPending ? 'Удаляю…' : 'Удалить навсегда'}
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>
          Отмена
        </Button>
      </div>
    </div>
  )
}

function ClientModal({ id, onClose }: { id: number; onClose: () => void }) {
  const { data: client, isPending } = useQuery({
    queryKey: ['client', id],
    queryFn: () => panel.client(id),
  })

  return (
    <Modal
      title={client ? clientName(client) : 'Клиент'}
      icon={<Contact className="size-5" />}
      onClose={onClose}
    >
      {isPending || !client ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="Обзор">
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-xs text-muted">Telegram ID</dt>
                <dd className="font-mono">{client.telegram_id}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Баланс</dt>
                <dd className="text-lg font-semibold tabular-nums">{rub(client.balance_rub)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">В боте с</dt>
                <dd>{dateTime(client.created_at)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Последняя активность</dt>
                <dd>{dateTime(client.last_seen_at)}</dd>
              </div>
              {client.pending_discount_percent > 0 && (
                <div className="col-span-2">
                  <dt className="text-xs text-muted">Скидка по промокоду</dt>
                  <dd>{client.pending_discount_percent}% на следующую оплату</dd>
                </div>
              )}
              {client.has_stopped_bot && (
                <div className="col-span-2 text-danger">Клиент заблокировал бота</div>
              )}
            </dl>
            <div className="space-y-1">
              <span className="text-xs text-muted">Подписки</span>
              {client.subscriptions_list.length === 0 && (
                <p className="text-sm text-muted">Нет ни одной подписки</p>
              )}
              {client.subscriptions_list.map((s) => {
                const expiry = untilExpiry(s.expire_at)
                return (
                  <p key={s.id} className="text-sm">
                    <span className="font-mono text-xs">{s.username}</span>{' '}
                    <span className={expiry.expired ? 'text-danger' : 'text-muted'}>
                      — {expiry.text}
                    </span>
                  </p>
                )
              })}
            </div>
          </Section>

          <Section title="Начислить или списать баланс">
            <BalanceForm client={client} />
          </Section>

          <div className="lg:col-span-2">
            <Section title="Персональные тарифы">
              <PersonalPlans client={client} />
            </Section>
          </div>

          <Section title="История баланса">
            {client.transactions.length === 0 ? (
              <p className="text-sm text-muted">Операций пока не было</p>
            ) : (
              <ul className="divide-y divide-border/60">
                {client.transactions.map((t) => (
                  <li key={t.id} className="flex items-start justify-between gap-3 py-2 text-sm">
                    <div className="min-w-0">
                      <p>{TX_LABEL[t.type] ?? t.type}</p>
                      <p className="truncate text-xs text-muted">
                        {dateTime(t.created_at)}
                        {t.description && ` · ${t.description}`}
                      </p>
                    </div>
                    <span
                      className={`shrink-0 tabular-nums ${
                        t.amount_rub > 0 ? 'text-success' : 'text-danger'
                      }`}
                    >
                      {t.amount_rub > 0 ? '+' : ''}
                      {rub(t.amount_rub)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <div className="lg:col-span-2">
            <DeleteClient client={client} onDeleted={onClose} />
          </div>

          <Section title="Покупки">
            {client.purchases.length === 0 ? (
              <p className="text-sm text-muted">Покупок пока не было</p>
            ) : (
              <ul className="divide-y divide-border/60">
                {client.purchases.map((p) => (
                  <li key={p.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                    <span>
                      {SOURCE_LABEL[p.source] ?? p.source} · {p.days} дн.
                      <span className="block text-xs text-muted">{dateTime(p.created_at)}</span>
                    </span>
                    <span className="tabular-nums">{p.amount_rub ? rub(p.amount_rub) : '—'}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      )}
    </Modal>
  )
}

export default function Clients() {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [openId, setOpenId] = useState<number | null>(null)

  const { data, isPending } = useQuery({
    queryKey: ['clients', query, page],
    queryFn: () => panel.clients({ search: query, offset: page * PAGE_SIZE, limit: PAGE_SIZE }),
  })
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Клиенты бота</h1>
        <p className="mt-1 text-sm text-muted">
          Люди из Telegram-бота: баланс, подписки, персональные тарифы.
        </p>
      </div>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          setPage(0)
          setQuery(search.trim())
        }}
      >
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Telegram ID, @username или имя"
        />
        <Button type="submit" variant="ghost">
          <Search className="size-4" />
          Найти
        </Button>
      </form>

      <Card className="p-0">
        {isPending ? (
          <p className="p-6 text-sm text-muted">Загрузка…</p>
        ) : !data?.items.length ? (
          <p className="p-6 text-sm text-muted">
            {query ? 'Никого не нашлось' : 'Клиентов пока нет — они появятся после /start в боте'}
          </p>
        ) : (
          <ul className="divide-y divide-border/60">
            {data.items.map((c) => {
              const expiry = untilExpiry(c.expire_at)
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => setOpenId(c.id)}
                    className="flex w-full items-center gap-4 px-5 py-3 text-left transition-colors hover:bg-surface-hover"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{clientName(c)}</p>
                      <p className="truncate text-xs text-muted">
                        {c.telegram_id} · подписок: {c.subscriptions}
                        {c.expire_at && ` · ${expiry.expired ? 'истекла' : `ещё ${expiry.text}`}`}
                      </p>
                    </div>
                    <span className="flex shrink-0 items-center gap-1.5 text-sm tabular-nums">
                      <Wallet className="size-4 text-muted" />
                      {rub(c.balance_rub)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </Card>

      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-muted">
          <span>Всего: {number(data.total)}</span>
          <div className="flex items-center gap-2">
            <Button variant="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Назад
            </Button>
            <span>
              {page + 1} / {pages}
            </span>
            <Button
              variant="ghost"
              disabled={page + 1 >= pages}
              onClick={() => setPage(page + 1)}
            >
              Вперёд
            </Button>
          </div>
        </div>
      )}

      {openId !== null && <ClientModal id={openId} onClose={() => setOpenId(null)} />}
    </div>
  )
}
