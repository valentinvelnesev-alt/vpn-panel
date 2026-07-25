import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type Promo, type PromoInput } from '@/lib/api'
import { number } from '@/lib/format'

const EMPTY: PromoInput = {
  code: '',
  bonus_days: 3,
  discount_percent: 0,
  max_uses: null,
  expires_at: null,
  is_active: true,
}

function PromoForm({
  initial,
  onSubmit,
  onCancel,
  pending,
}: {
  initial: PromoInput
  onSubmit: (data: PromoInput) => void
  onCancel: () => void
  pending: boolean
}) {
  const [form, setForm] = useState(initial)
  const set = <K extends keyof PromoInput>(key: K, value: PromoInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  return (
    <form
      className="mt-3 space-y-4 rounded-2xl border p-4"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit(form)
      }}
    >
      <Field label="Код">
        <Input
          value={form.code}
          onChange={(e) => set('code', e.target.value.toUpperCase())}
          placeholder="WELCOME10"
          required
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Бонусные дни">
          <Input
            type="number"
            min={0}
            value={form.bonus_days}
            onChange={(e) => set('bonus_days', Number(e.target.value))}
          />
        </Field>
        <Field label="Макс. активаций" hint="Пусто — без ограничения">
          <Input
            type="number"
            min={1}
            value={form.max_uses ?? ''}
            onChange={(e) =>
              set('max_uses', e.target.value ? Number(e.target.value) : null)
            }
          />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4"
          checked={form.is_active}
          onChange={(e) => set('is_active', e.target.checked)}
        />
        Активен
      </label>
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

export default function BotPromo() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<number | 'new' | null>(null)

  const { data: codes } = useQuery({ queryKey: ['promo'], queryFn: panel.promoCodes })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['promo'] })
    setEditing(null)
  }

  const create = useMutation({ mutationFn: panel.createPromo, onSuccess: invalidate })
  const update = useMutation({
    mutationFn: ({ id, data }: { id: number; data: PromoInput }) =>
      panel.updatePromo(id, data),
    onSuccess: invalidate,
  })
  const remove = useMutation({ mutationFn: panel.deletePromo, onSuccess: invalidate })

  const stripId = ({ id: _id, uses_count: _u, ...rest }: Promo): PromoInput => rest

  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-medium">Промокоды</h2>
          <p className="mt-1 text-sm text-muted">
            Бонусные дни к подписке по коду. Один код — одна активация на пользователя.
          </p>
        </div>
        <Button variant="ghost" onClick={() => setEditing('new')}>
          <Plus className="size-4" />
          Добавить
        </Button>
      </div>

      {editing === 'new' && (
        <PromoForm
          initial={EMPTY}
          pending={create.isPending}
          onSubmit={(data) => create.mutate(data)}
          onCancel={() => setEditing(null)}
        />
      )}
      {create.isError && (
        <p className="mt-2 text-sm text-danger">{(create.error as Error).message}</p>
      )}

      <div className="mt-4 space-y-2">
        {codes?.length === 0 && editing !== 'new' && (
          <p className="text-sm text-muted">Промокодов пока нет.</p>
        )}

        {codes?.map((promo) =>
          editing === promo.id ? (
            <PromoForm
              key={promo.id}
              initial={stripId(promo)}
              pending={update.isPending}
              onSubmit={(data) => update.mutate({ id: promo.id, data })}
              onCancel={() => setEditing(null)}
            />
          ) : (
            <div
              key={promo.id}
              className="flex items-center justify-between gap-4 rounded-2xl border px-3 py-2"
            >
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => setEditing(promo.id)}
              >
                <span className="font-mono font-medium">{promo.code}</span>
                <span className="text-sm text-muted">
                  {' '}
                  · +{promo.bonus_days} дн. · использован {number(promo.uses_count)}
                  {promo.max_uses ? ` / ${promo.max_uses}` : ''} раз
                  {!promo.is_active && ' · выключен'}
                </span>
              </button>
              <button
                type="button"
                onClick={() => remove.mutate(promo.id)}
                className="text-muted hover:text-danger"
                aria-label="Удалить промокод"
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
