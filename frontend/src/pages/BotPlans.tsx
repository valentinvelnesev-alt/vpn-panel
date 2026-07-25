import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type Plan, type PlanInput } from '@/lib/api'

const EMPTY: PlanInput = {
  title: '',
  days: 30,
  price_rub: 199,
  squad_uuids: [],
  hwid_limit: 3,
  traffic_limit_bytes: 0,
  is_active: true,
  sort_order: 0,
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

      <Field label="Лимит устройств">
        <Input
          type="number"
          min={1}
          value={form.hwid_limit}
          onChange={(e) => set('hwid_limit', Number(e.target.value))}
        />
      </Field>

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
  )
}
