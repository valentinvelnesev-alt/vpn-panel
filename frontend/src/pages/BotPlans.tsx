import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderPlus, Pencil, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import {
  panel,
  type BotStatus,
  type Plan,
  type PlanCategory,
  type PlanInput,
} from '@/lib/api'
import { usePanelTerms } from '@/lib/panelKind'

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
  const terms = usePanelTerms()
  // Сквады (у 3x-ui — инбаунды) подтягиваем из панели, чтобы не вводить UUID руками.
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
        <Field label="Лимит трафика, ГБ" hint="0 — безлимит. При лимите клиент сможет докупать трафик пакетами.">
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
        <span className="text-sm font-medium">{terms.squadsOf}</span>
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
            {terms.squads} не загрузились — проверьте подключение к {terms.name}.
          </p>
        )}
        {form.squad_uuids.length === 0 && (
          <p className="mt-1 text-xs text-danger">
            {terms.noSquad} — купившие этот тариф не получат доступ ни к одной ноде.
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

/** Действующая общая скидка, 0 — нет или закончилась. */
export function discountActive(status: BotStatus | undefined): number {
  if (!status || status.discount_percent <= 0) return 0
  if (status.discount_until && new Date(status.discount_until).getTime() <= Date.now()) return 0
  return status.discount_percent
}

/** Цена со скидкой — тем же правилом, что в боте: круглая цена остаётся
 * круглой (199 ₽ −15% = 169 ₽), не дешевле 1 ₽. */
export function discountedRub(priceRub: number, percent: number): number {
  const kopeks = Math.round(priceRub * 100)
  if (!percent) return priceRub
  let price = (kopeks * (100 - percent)) / 100
  if (kopeks % 100 === 0) price = Math.floor(price / 100) * 100
  return Math.max(100, Math.round(price)) / 100
}

function daysLeft(iso: string | null): number | null {
  if (!iso) return null
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000))
}

function DiscountCard() {
  const queryClient = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['bot'], queryFn: panel.botStatus })
  const [percent, setPercent] = useState(0)
  const [days, setDays] = useState('')

  useEffect(() => {
    if (!status) return
    setPercent(status.discount_percent)
    const left = daysLeft(status.discount_until)
    setDays(left ? String(left) : '')
  }, [status])

  const save = useMutation({
    mutationFn: (json: { percent: number; days: number | null }) => panel.saveDiscount(json),
    onSuccess: (data) => queryClient.setQueryData(['bot'], data),
  })

  const active = discountActive(status)
  const left = daysLeft(status?.discount_until ?? null)

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium">Скидка на все тарифы</h2>
          <p className="mt-1 text-sm text-muted">
            В боте у тарифа будет «169 ₽ вместо 199 ₽». На персональные тарифы клиентов не
            действует. Со скидкой по промокоду не складывается — клиент получает большую.
          </p>
        </div>
        {active > 0 && (
          <span className="shrink-0 rounded-full bg-success/15 px-3 py-1 text-xs text-success">
            −{active}%{left !== null ? `, ещё ${left} дн.` : ', бессрочно'}
          </span>
        )}
      </div>

      <form
        className="mt-4 grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({ percent, days: days ? Number(days) : null })
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
        <Field label="На сколько дней" hint="Пусто — пока не выключите">
          <Input
            type="number"
            min={1}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            placeholder="например 7"
          />
        </Field>
        <div className="flex gap-2">
          <Button type="submit" disabled={save.isPending}>
            Сохранить
          </Button>
          {status && status.discount_percent > 0 && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => save.mutate({ percent: 0, days: null })}
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

function PlanRow({
  plan,
  discount,
  onEdit,
  onDelete,
}: {
  plan: Plan
  discount: number
  onEdit: () => void
  onDelete: () => void
}) {
  const price = discountedRub(plan.price_rub, discount)
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl border px-3 py-2">
      <button type="button" className="min-w-0 flex-1 text-left" onClick={onEdit}>
        <span className="font-medium">{plan.title}</span>
        <span className="text-sm text-muted">
          {' '}
          · {plan.days} дн. ·{' '}
          {price !== plan.price_rub ? (
            <>
              {price} ₽ <s>{plan.price_rub} ₽</s>
            </>
          ) : (
            <>{plan.price_rub} ₽</>
          )}{' '}
          · {plan.hwid_limit} устр. ·{' '}
          {plan.traffic_limit_bytes ? `${Math.round(plan.traffic_limit_bytes / GIB)} ГБ` : 'безлимит'}
          {!plan.is_active && ' · скрыт'}
        </span>
      </button>
      <button
        type="button"
        onClick={onDelete}
        className="text-muted hover:text-danger"
        aria-label="Удалить тариф"
      >
        <Trash2 className="size-4" />
      </button>
    </div>
  )
}

/** Блок одной категории: заголовок (переименовать / удалить) и её тарифы. */
function CategorySection({
  category,
  plans,
  discount,
  editing,
  setEditing,
  onSavePlan,
  onDeletePlan,
  pending,
  error,
}: {
  category: PlanCategory | null
  plans: Plan[]
  discount: number
  editing: number | string | null
  setEditing: (v: number | string | null) => void
  onSavePlan: (id: number | null, plan: PlanInput) => void
  onDeletePlan: (id: number) => void
  pending: boolean
  error: string | null
}) {
  const queryClient = useQueryClient()
  const [renaming, setRenaming] = useState(false)
  const [title, setTitle] = useState(category?.title ?? '')
  const newKey = `new-${category?.id ?? 'none'}`

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['plan-categories'] })
    queryClient.invalidateQueries({ queryKey: ['plans'] })
  }
  const rename = useMutation({
    mutationFn: () => panel.updatePlanCategory(category!.id, { title, sort_order: category!.sort_order }),
    onSuccess: () => {
      invalidate()
      setRenaming(false)
    },
  })
  const remove = useMutation({
    mutationFn: () => panel.deletePlanCategory(category!.id),
    onSuccess: invalidate,
  })

  return (
    <section className="rounded-2xl border border-border/60 p-3">
      <div className="flex items-center justify-between gap-3 px-1">
        {renaming && category ? (
          <form
            className="flex flex-1 gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              rename.mutate()
            }}
          >
            <Input value={title} onChange={(e) => setTitle(e.target.value)} required autoFocus />
            <Button type="submit" disabled={rename.isPending}>
              Сохранить
            </Button>
            <Button type="button" variant="ghost" onClick={() => setRenaming(false)}>
              Отмена
            </Button>
          </form>
        ) : (
          <>
            <h3 className="text-sm font-semibold">
              {category ? category.title : 'Без категории'}
              <span className="ml-2 font-normal text-muted">{plans.length} тариф(ов)</span>
            </h3>
            <div className="flex items-center gap-1">
              <Button variant="ghost" className="h-8 px-3" onClick={() => setEditing(newKey)}>
                <Plus className="size-4" />
                Тариф
              </Button>
              {category && (
                <>
                  <button
                    type="button"
                    onClick={() => setRenaming(true)}
                    className="rounded-full p-2 text-muted hover:text-fg"
                    aria-label="Переименовать категорию"
                  >
                    <Pencil className="size-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (
                        confirm(
                          `Удалить категорию «${category.title}»? Тарифы останутся, но окажутся «Без категории».`,
                        )
                      )
                        remove.mutate()
                    }}
                    className="rounded-full p-2 text-muted hover:text-danger"
                    aria-label="Удалить категорию"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </div>

      <div className="mt-2 space-y-2">
        {editing === newKey && (
          <PlanForm
            initial={{ ...EMPTY_PLAN, category_id: category?.id ?? null }}
            pending={pending}
            error={error}
            onSubmit={(plan) => onSavePlan(null, plan)}
            onCancel={() => setEditing(null)}
          />
        )}
        {plans.length === 0 && editing !== newKey && (
          <p className="px-1 text-sm text-muted">Пусто — нажмите «+ Тариф».</p>
        )}
        {plans.map((plan) =>
          editing === plan.id ? (
            <PlanForm
              key={plan.id}
              initial={plan}
              pending={pending}
              error={error}
              onSubmit={(data) => onSavePlan(plan.id, data)}
              onCancel={() => setEditing(null)}
            />
          ) : (
            <PlanRow
              key={plan.id}
              plan={plan}
              discount={discount}
              onEdit={() => setEditing(plan.id)}
              onDelete={() => onDeletePlan(plan.id)}
            />
          ),
        )}
      </div>
    </section>
  )
}

export default function BotPlans() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<number | string | null>(null)
  const [addingCategory, setAddingCategory] = useState(false)
  const [categoryTitle, setCategoryTitle] = useState('')

  const { data: plans } = useQuery({ queryKey: ['plans'], queryFn: panel.plans })
  const { data: categories } = useQuery({
    queryKey: ['plan-categories'],
    queryFn: panel.planCategories,
  })
  const { data: status } = useQuery({ queryKey: ['bot'], queryFn: panel.botStatus })
  const discount = discountActive(status)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['plans'] })
    setEditing(null)
  }
  const savePlan = useMutation({
    mutationFn: ({ id, plan }: { id: number | null; plan: PlanInput }) =>
      id === null ? panel.createPlan(plan) : panel.updatePlan(id, plan),
    onSuccess: invalidate,
  })
  const remove = useMutation({ mutationFn: panel.deletePlan, onSuccess: invalidate })
  const createCategory = useMutation({
    mutationFn: () => panel.createPlanCategory({ title: categoryTitle, sort_order: 0 }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plan-categories'] })
      setAddingCategory(false)
      setCategoryTitle('')
    },
  })

  const all = plans ?? []
  const uncategorized = all.filter((p) => p.category_id === null)
  const known = new Set((categories ?? []).map((c) => c.id))
  // Тариф со ссылкой на удалённую категорию тоже показываем «без категории».
  const orphan = all.filter((p) => p.category_id !== null && !known.has(p.category_id))
  const usedCategories = (categories ?? []).filter((c) => all.some((p) => p.category_id === c.id))
  const tabs = usedCategories.length + (uncategorized.length + orphan.length ? 1 : 0)

  const sectionProps = {
    discount,
    editing,
    setEditing,
    onSavePlan: (id: number | null, plan: PlanInput) => savePlan.mutate({ id, plan }),
    onDeletePlan: (id: number) => remove.mutate(id),
    pending: savePlan.isPending,
    error: savePlan.isError ? (savePlan.error as Error).message : null,
  }

  return (
    <div className="space-y-6">
      <DiscountCard />
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-medium">Тарифы</h2>
            <p className="mt-1 text-sm text-muted">
              {tabs > 1
                ? 'В боте клиент сначала выберет категорию, затем тариф внутри неё.'
                : 'Категорий одна или нет — в боте будет один общий список тарифов.'}{' '}
              Категории нужны, если тарифы различаются по смыслу (например «VPN» и «VPN + LTE»).
            </p>
          </div>
          <Button variant="ghost" onClick={() => setAddingCategory(true)}>
            <FolderPlus className="size-4" />
            Категория
          </Button>
        </div>

        {addingCategory && (
          <form
            className="mt-3 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              createCategory.mutate()
            }}
          >
            <Input
              value={categoryTitle}
              onChange={(e) => setCategoryTitle(e.target.value)}
              placeholder="Например: VPN + LTE"
              required
              autoFocus
            />
            <Button type="submit" disabled={createCategory.isPending}>
              Создать
            </Button>
            <Button type="button" variant="ghost" onClick={() => setAddingCategory(false)}>
              Отмена
            </Button>
          </form>
        )}

        <div className="mt-4 space-y-3">
          {(categories ?? []).map((category) => (
            <CategorySection
              key={category.id}
              category={category}
              plans={all.filter((p) => p.category_id === category.id)}
              {...sectionProps}
            />
          ))}
          {(uncategorized.length > 0 || orphan.length > 0 || !categories?.length) && (
            <CategorySection category={null} plans={[...uncategorized, ...orphan]} {...sectionProps} />
          )}
        </div>
      </Card>
    </div>
  )
}
