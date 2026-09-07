import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type TrafficPackage } from '@/lib/api'

type Draft = Omit<TrafficPackage, 'id'>

const EMPTY: Draft = {
  title: '',
  traffic_gb: 50,
  price_rub: 99,
  is_active: true,
  sort_order: 0,
}

function PackageForm({
  initial,
  onCancel,
  onSaved,
  id,
}: {
  initial: Draft
  onCancel: () => void
  onSaved: () => void
  id?: number
}) {
  const [form, setForm] = useState<Draft>(initial)
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const save = useMutation({
    mutationFn: () =>
      id === undefined
        ? panel.createTrafficPackage(form)
        : panel.updateTrafficPackage(id, form),
    onSuccess: onSaved,
  })

  return (
    <form
      className="space-y-4 rounded-2xl bg-bg p-4"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <Field label="Название" hint="Видно в боте на кнопке, например «50 ГБ»">
        <Input
          value={form.title}
          onChange={(e) => set('title', e.target.value)}
          placeholder="50 ГБ"
          required
        />
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Объём, ГБ">
          <Input
            type="number"
            min={1}
            value={form.traffic_gb}
            onChange={(e) => set('traffic_gb', Number(e.target.value))}
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
        <Field label="Порядок в списке">
          <Input
            type="number"
            value={form.sort_order}
            onChange={(e) => set('sort_order', Number(e.target.value))}
          />
        </Field>
        <label className="flex items-center gap-2 self-end text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={form.is_active}
            onChange={(e) => set('is_active', e.target.checked)}
          />
          Показывать в боте
        </label>
      </div>

      {save.isError && <p className="text-sm text-danger">{(save.error as Error).message}</p>}

      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending}>
          Сохранить
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Отмена
        </Button>
      </div>
    </form>
  )
}

export default function BotTraffic() {
  const queryClient = useQueryClient()
  const { data: packages } = useQuery({
    queryKey: ['traffic-packages'],
    queryFn: panel.trafficPackages,
  })
  const [editing, setEditing] = useState<number | 'new' | null>(null)

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['traffic-packages'] })
    setEditing(null)
  }

  const remove = useMutation({
    mutationFn: (id: number) => panel.deleteTrafficPackage(id),
    onSuccess: refresh,
  })

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium">Пакеты трафика</h2>
          <p className="mt-1 text-sm text-muted">
            Клиент докупает их в боте: «Подписка» → нужный ключ → «Докупить трафик».
            Пакет прибавляется к лимиту подписки. У подписок с безлимитным трафиком
            кнопка не показывается — прибавлять там нечего.
          </p>
        </div>
        {editing === null && (
          <Button type="button" onClick={() => setEditing('new')}>
            <Plus className="size-4" /> Добавить
          </Button>
        )}
      </div>

      {editing === 'new' && (
        <div className="mt-4">
          <PackageForm initial={EMPTY} onCancel={() => setEditing(null)} onSaved={refresh} />
        </div>
      )}

      <div className="mt-4 space-y-2">
        {packages?.length === 0 && editing === null && (
          <p className="text-sm text-muted">
            Пакетов пока нет — кнопка «Докупить трафик» в боте не показывается.
          </p>
        )}

        {packages?.map((item) =>
          editing === item.id ? (
            <PackageForm
              key={item.id}
              id={item.id}
              initial={item}
              onCancel={() => setEditing(null)}
              onSaved={refresh}
            />
          ) : (
            <div
              key={item.id}
              className="flex items-center justify-between gap-3 rounded-2xl bg-bg px-3 py-2 text-sm"
            >
              <div>
                <span className="font-medium">{item.title}</span>
                <span className="text-muted">
                  {' '}
                  · {item.traffic_gb} ГБ · {item.price_rub} ₽
                  {item.is_active ? '' : ' · скрыт'}
                </span>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button type="button" variant="ghost" onClick={() => setEditing(item.id)}>
                  <Pencil className="size-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => remove.mutate(item.id)}
                  disabled={remove.isPending}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ),
        )}
      </div>
    </Card>
  )
}
