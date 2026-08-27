import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type Plan, type PlanCategoryInput, type PlanInput } from '@/lib/api'

const EMPTY: PlanInput = {
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

function PlanForm({
  initial,
  onSubmit,
  onCancel,
  pending,
}: {
  initial: PlanInput
  onSubmit: (plan: PlanInput) => void
  onCancel: () => void
  pending: boolean
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

export default function BotPlans() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<number | 'new' | null>(null)

  const { data: plans } = useQuery({ queryKey: ['plans'], queryFn: panel.plans })

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
          initial={EMPTY}
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
                  · {plan.days} дн. · {plan.price_rub} ₽ · {plan.hwid_limit} устр.
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
