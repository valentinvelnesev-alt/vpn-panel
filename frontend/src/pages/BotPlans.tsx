import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import {
  panel,
  type BotStatus,
  type Plan,
  type PlanCategoryInput,
  type PlanInput,
} from '@/lib/api'

const GIB = 1024 ** 3

export const EMPTY_PLAN: PlanInput = {
  title: '',
  days: 30,
  price_rub: 199,
  squad_uuids: [],
  hwid_limit: 3,
  traffic_limit_bytes: 0,
  category_id: null,
  is_active: true,
  sort_order: 0,
}

const EMPTY_CATEGORY: PlanCategoryInput = { title: '', sort_order: 0 }

function CategoriesManager() {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editTitle, setEditTitle] = useState('')

  const { data: categories } = useQuery({
    queryKey: ['plan-categories'],
    queryFn: panel.planCategories,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['plan-categories'] })
    queryClient.invalidateQueries({ queryKey: ['plans'] })
  }

  const create = useMutation({
    mutationFn: (json: PlanCategoryInput) => panel.createPlanCategory(json),
    onSuccess: () => {
      invalidate()
      setAdding(false)
      setNewTitle('')
    },
  })
  const update = useMutation({
    mutationFn: ({ id, json }: { id: number; json: PlanCategoryInput }) =>
      panel.updatePlanCategory(id, json),
    onSuccess: () => {
      invalidate()
      setEditingId(null)
    },
  })
  const remove = useMutation({ mutationFn: panel.deletePlanCategory, onSuccess: invalidate })

  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-medium">Категории тарифов</h2>
          <p className="mt-1 text-sm text-muted">
            Если категорий больше одной, бот сначала предложит выбрать
            категорию, а затем — тариф внутри неё. Удаление категории не
            удаляет тарифы — они просто остаются без категории.
          </p>
        </div>
        <Button variant="ghost" onClick={() => setAdding(true)}>
          <Plus className="size-4" />
          Добавить
        </Button>
      </div>

      {adding && (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate({ ...EMPTY_CATEGORY, title: newTitle })
          }}
        >
          <Input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="Например: VPN + LTE"
            required
            autoFocus
          />
          <Button type="submit" disabled={create.isPending}>
            Сохранить
          </Button>
          <Button type="button" variant="ghost" onClick={() => setAdding(false)}>
            Отмена
          </Button>
        </form>
      )}

      <div className="mt-3 space-y-2">
        {categories?.length === 0 && !adding && (
          <p className="text-sm text-muted">
            Категорий нет — все тарифы показываются одним списком.
          </p>
        )}
        {categories?.map((cat) =>
          editingId === cat.id ? (
            <form
              key={cat.id}
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                update.mutate({ id: cat.id, json: { title: editTitle, sort_order: cat.sort_order } })
              }}
            >
              <Input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                required
                autoFocus
              />
              <Button type="submit" disabled={update.isPending}>
                Сохранить
              </Button>
              <Button type="button" variant="ghost" onClick={() => setEditingId(null)}>
                Отмена
              </Button>
            </form>
          ) : (
            <div
              key={cat.id}
              className="flex items-center justify-between gap-4 rounded-2xl border px-3 py-2"
            >
              <button
                type="button"
                className="min-w-0 flex-1 text-left font-medium"
                onClick={() => {
                  setEditingId(cat.id)
                  setEditTitle(cat.title)
                }}
              >
                {cat.title}
              </button>
              <button
                type="button"
                onClick={() => remove.mutate(cat.id)}
                className="text-muted hover:text-danger"
                aria-label="Удалить категорию"
              >
                <Trash2 className="size-4" />
              </button>
            </div>
          ),
        )}
      </div>
    </Card>
  )
}

export function PlanForm({
  initial,
  onSubmit,
  onCancel,
  pending,
  personal = false,
  error,
}: {
  initial: PlanInput
  onSubmit: (plan: PlanInput) => void
  onCancel: () => void
  pending: boolean
  /** Персональный тариф клиента: без категории, зато с переключателем «активен». */
  personal?: boolean
  error?: string | null
}) {
  const [form, setForm] = useState(initial)
  // Сквады подтягиваем из Remnawave, чтобы не вводить UUID руками.
  const { data: squads } = useQuery({ queryKey: ['squads'], queryFn: panel.squads })
  const { data: categories } = useQuery({
    queryKey: ['plan-categories'],
    queryFn: panel.planCategories,
  })

  const set = <K extends keyof PlanInput>(key: K, value: PlanInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const toggleSquad = (uuid: string) =>
    set(
      'squad_uuids',
      form.squad_uuids.includes(uuid)
        ? form.squad_uuids.filter((s) => s !== uuid)
        : [...form.squad_uuids, uuid],
    )

  return (
    <form
      className="mt-3 space-y-4 rounded-2xl border p-4"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit(form)
      }}
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Название">
          <Input
            value={form.title}
            onChange={(e) => set('title', e.target.value)}
            placeholder="Месяц"
            required
          />
        </Field>
        <Field label="Дней">
          <Input
            type="number"
            min={1}
            value={form.days}
            onChange={(e) => set('days', Number(e.target.value))}
          />
        </Field>
        <Field label="Цена, ₽">
          <Input
            type="number"
            min={0}
            step="0.01"
            value={form.price_rub}
            onChange={(e) => set('price_rub', Number(e.target.value))}
          />
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Лимит устройств">
          <Input
            type="number"
            min={1}
            value={form.hwid_limit}
            onChange={(e) => set('hwid_limit', Number(e.target.value))}
          />
        </Field>
        <Field label="Лимит трафика, ГБ" hint="0 — без ограничений">
          <Input
            type="number"
            min={0}
            value={Math.round(form.traffic_limit_bytes / GIB)}
            onChange={(e) => set('traffic_limit_bytes', Number(e.target.value) * GIB)}
          />
        </Field>
        {personal ? (
          <label className="flex items-center gap-2 self-end pb-2 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={form.is_active}
              onChange={(e) => set('is_active', e.target.checked)}
            />
            Показывать клиенту в боте
          </label>
        ) : (
        <Field label="Категория (необязательно)">
          <select
            className="h-10 w-full rounded-lg border bg-surface px-3 text-sm"
            value={form.category_id ?? ''}
            onChange={(e) =>
              set('category_id', e.target.value ? Number(e.target.value) : null)
            }
          >
            <option value="">Без категории</option>
            {categories?.map((cat) => (
              <option key={cat.id} value={cat.id}>
                {cat.title}
              </option>
            ))}
          </select>
        </Field>
        )}
      </div>

      <div>
        <span className="text-sm font-medium">Сквады Remnawave</span>
        {squads?.length ? (
          <div className="mt-2 flex flex-wrap gap-2">
            {squads.map((squad) => (
              <button
                key={squad.uuid}
                type="button"
                onClick={() => toggleSquad(squad.uuid)}
                className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                  form.squad_uuids.includes(squad.uuid)
                    ? 'border-accent bg-accent/10 text-accent'
                    : 'hover:bg-surface-hover'
                }`}
              >
                {squad.name}
              </button>
            ))}
          </div>
        ) : (
          <p className="mt-1 text-xs text-muted">
            Сквады не загрузились — проверьте подключение к Remnawave.
          </p>
        )}
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          Сохранить
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Отмена
        </Button>
      </div>
    </form>
  )
}

/** Цена с учётом общей скидки — как её увидит клиент в боте. */
export function discountActive(status: BotStatus | undefined): number {
  if (!status || status.discount_percent <= 0) return 0
  if (status.discount_until && new Date(status.discount_until).getTime() <= Date.now()) return 0
  return status.discount_percent
}

function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function DiscountCard() {
  const queryClient = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['bot'], queryFn: panel.botStatus })
  const [percent, setPercent] = useState(0)
  const [until, setUntil] = useState('')

  useEffect(() => {
    if (!status) return
    setPercent(status.discount_percent)
    setUntil(toLocalInput(status.discount_until))
  }, [status])

  const save = useMutation({
    mutationFn: (json: { percent: number; until: string | null }) => panel.saveDiscount(json),
    onSuccess: (data) => queryClient.setQueryData(['bot'], data),
  })

  const active = discountActive(status)
  const expired = !!status && status.discount_percent > 0 && active === 0

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium">Скидка на все тарифы</h2>
          <p className="mt-1 text-sm text-muted">
            Бот покажет старую цену зачёркнутой и новую рядом. На персональные тарифы
            клиентов не действует — у них и так своя цена. Скидка по промокоду с ней не
            складывается: клиент получает большую из двух.
          </p>
        </div>
        {active > 0 && (
          <span className="shrink-0 rounded-full bg-success/15 px-3 py-1 text-xs text-success">
            −{active}% действует
          </span>
        )}
        {expired && (
          <span className="shrink-0 rounded-full bg-muted/20 px-3 py-1 text-xs text-muted">
            закончилась
          </span>
        )}
      </div>

      <form
        className="mt-4 grid gap-4 sm:grid-cols-[1fr_1.4fr_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({ percent, until: until ? new Date(until).toISOString() : null })
        }}
      >
        <Field label="Скидка, %">
          <Input
            type="number"
            min={0}
            max={95}
            value={percent}
            onChange={(e) => setPercent(Number(e.target.value))}
          />
        </Field>
        <Field label="Действует до (необязательно)">
          <Input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
        </Field>
        <div className="flex gap-2">
          <Button type="submit" disabled={save.isPending}>
            Сохранить
          </Button>
          {status && status.discount_percent > 0 && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => save.mutate({ percent: 0, until: null })}
              disabled={save.isPending}
            >
              Выключить
            </Button>
          )}
        </div>
      </form>
      {save.isError && <p className="mt-2 text-sm text-danger">{(save.error as Error).message}</p>}
    </Card>
  )
}

export default function BotPlans() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<number | 'new' | null>(null)

  const { data: plans } = useQuery({ queryKey: ['plans'], queryFn: panel.plans })
  const { data: status } = useQuery({ queryKey: ['bot'], queryFn: panel.botStatus })
  const discount = discountActive(status)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['plans'] })
    setEditing(null)
  }

  const create = useMutation({ mutationFn: panel.createPlan, onSuccess: invalidate })
  const update = useMutation({
    mutationFn: ({ id, plan }: { id: number; plan: PlanInput }) =>
      panel.updatePlan(id, plan),
    onSuccess: invalidate,
  })
  const remove = useMutation({ mutationFn: panel.deletePlan, onSuccess: invalidate })

  const stripId = ({ id: _id, ...rest }: Plan): PlanInput => rest

  return (
    <div className="space-y-6">
      <DiscountCard />
      <CategoriesManager />
      <Card>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-medium">Тарифы</h2>
          <p className="mt-1 text-sm text-muted">
            Срок, цена и доступные сквады. Бот подхватит изменения сразу.
          </p>
        </div>
        <Button variant="ghost" onClick={() => setEditing('new')}>
          <Plus className="size-4" />
          Добавить
        </Button>
      </div>

      {editing === 'new' && (
        <PlanForm
          initial={EMPTY_PLAN}
          pending={create.isPending}
          onSubmit={(plan) => create.mutate(plan)}
          onCancel={() => setEditing(null)}
        />
      )}

      <div className="mt-4 space-y-2">
        {plans?.length === 0 && editing !== 'new' && (
          <p className="text-sm text-muted">
            Тарифов пока нет — бот покажет только пробный период.
          </p>
        )}

        {plans?.map((plan) =>
          editing === plan.id ? (
            <PlanForm
              key={plan.id}
              initial={stripId(plan)}
              pending={update.isPending}
              onSubmit={(data) => update.mutate({ id: plan.id, plan: data })}
              onCancel={() => setEditing(null)}
            />
          ) : (
            <div
              key={plan.id}
              className="flex items-center justify-between gap-4 rounded-2xl border px-3 py-2"
            >
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => setEditing(plan.id)}
              >
                <span className="font-medium">{plan.title}</span>
                <span className="text-sm text-muted">
                  {' '}
                  · {plan.days} дн. ·{' '}
                  {discount > 0 ? (
                    <>
                      <s>{plan.price_rub} ₽</s>{' '}
                      {Math.floor(plan.price_rub * (100 - discount)) / 100} ₽
                    </>
                  ) : (
                    <>{plan.price_rub} ₽</>
                  )}{' '}
                  · {plan.hwid_limit} устр.
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
      </div>
      </Card>
    </div>
  )
}
