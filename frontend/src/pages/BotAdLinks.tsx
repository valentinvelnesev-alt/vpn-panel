import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Toggle } from '@/components/page'
import { Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button, Card, CopyAction, DeleteAction, Field, Input, RowAction } from '@/components/ui'
import { panel, type AdLink, type AdLinkInput, type AdLinksState } from '@/lib/api'
import { number } from '@/lib/format'

const EMPTY: AdLinkInput = { name: '', prefix: '', percent: 50, owner: '', is_active: true, payout_mode: 'balance' }

const rub = (value: number) => `${number(Math.round(value * 100) / 100)} ₽`

// Рекламные ссылки для рекламодателей: выключены, пока админ не включит.
// Блок стоит в самом низу страницы — нужен не всем.
export default function BotAdLinks() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['ad-links'], queryFn: panel.adLinks })
  const [form, setForm] = useState<AdLinkInput>(EMPTY)
  const [editing, setEditing] = useState<number | null>(null)

  const refresh = (state: AdLinksState) => queryClient.setQueryData(['ad-links'], state)
  const reset = () => {
    setForm(EMPTY)
    setEditing(null)
  }

  const toggle = useMutation({ mutationFn: (on: boolean) => panel.setAdLinksEnabled(on), onSuccess: refresh })
  const save = useMutation({
    mutationFn: () =>
      editing === null ? panel.createAdLink(form) : panel.updateAdLink(editing, form),
    onSuccess: (state) => {
      refresh(state)
      reset()
    },
  })
  const payout = useMutation({
    mutationFn: (id: number) => panel.payoutAdLink(id),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (id: number) => panel.deleteAdLink(id),
    onSuccess: refresh,
  })

  if (!data) return null

  const set = <K extends keyof AdLinkInput>(key: K, value: AdLinkInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const edit = (link: AdLink) => {
    setEditing(link.id)
    setForm({
      name: link.name,
      prefix: link.prefix,
      percent: link.percent,
      owner: link.owner,
      is_active: link.is_active,
      payout_mode: link.payout_mode,
    })
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium">Рекламные ссылки</h2>
          <p className="mt-1 text-sm text-muted">
            Для рекламодателей: ссылка с префиксом и процентом. Владелец получает этот процент с каждой
            оплаты пришедших по ней людей на баланс в боте и видит статистику по кнопке «Статистика»
            в меню бота.
          </p>
        </div>
        <Button
          type="button"
          variant={data.enabled ? 'ghost' : 'primary'}
          onClick={() => toggle.mutate(!data.enabled)}
          disabled={toggle.isPending}
        >
          {data.enabled ? 'Выключить' : 'Включить'}
        </Button>
      </div>

      {data.enabled && (
        <div className="mt-4 space-y-6">
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault()
              save.mutate()
            }}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Название">
                <Input
                  value={form.name}
                  onChange={(e) => set('name', e.target.value)}
                  placeholder="Канал «Про VPN»"
                />
              </Field>
              <Field label="Префикс" hint="Латиница, цифры, «_» и «-». Ссылка: …?start=префикс">
                <Input
                  value={form.prefix}
                  onChange={(e) => set('prefix', e.target.value.toLowerCase())}
                  placeholder="provpn"
                />
              </Field>
              <Field label="Владелец" hint="Telegram ID или @username. Он увидит кнопку «Статистика».">
                <Input
                  value={form.owner}
                  onChange={(e) => set('owner', e.target.value)}
                  placeholder="@username или 123456789"
                />
              </Field>
              <Field label="Процент с покупок">
                <Input
                  type="number"
                  min={1}
                  max={100}
                  value={form.percent}
                  onChange={(e) => set('percent', Number(e.target.value))}
                />
              </Field>
            </div>
            <Field
              label="Выплата владельцу"
              hint={
                form.payout_mode === 'balance'
                  ? 'Доля сразу падает на баланс владельца в боте. «Выплатил» списывает её обратно.'
                  : 'На баланс ничего не идёт: сумма только копится в статистике, вы платите сами и нажимаете «Выплатил».'
              }
            >
              <select
                className="w-full rounded-2xl border bg-bg px-3 py-2 text-sm"
                value={form.payout_mode}
                onChange={(e) => set('payout_mode', e.target.value as AdLinkInput['payout_mode'])}
              >
                <option value="balance">На баланс в боте</option>
                <option value="manual">Только показывать (плачу сам)</option>
              </select>
            </Field>
            <Toggle
              checked={form.is_active}
              onChange={(value) => set('is_active', value)}
              label="Активна"
            />
            {save.isError && <p className="text-sm text-danger">{(save.error as Error).message}</p>}
            <div className="flex gap-2">
              <Button
                type="submit"
                disabled={save.isPending || !form.name.trim() || !form.prefix || !form.owner.trim()}
              >
                {editing === null ? 'Добавить' : 'Сохранить'}
              </Button>
              {editing !== null && (
                <Button type="button" variant="ghost" onClick={reset}>
                  Отмена
                </Button>
              )}
            </div>
          </form>

          {data.links.length > 0 && (
            <ul className="divide-y rounded-2xl border">
              {data.links.map((link) => (
                <li key={link.id} className="space-y-2 p-3 text-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <span className="font-medium">{link.name}</span>
                      <span className="text-muted">
                        {' '}
                        · {link.percent}% · {link.owner}
                        {!link.owner_found && ' (ещё не заходил в бота)'}
                        {!link.is_active && ' · выключена'}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {link.url && <CopyAction text={link.url} />}
                      <RowAction
                        icon={<Pencil className="size-4" />}
                        label="Изменить"
                        onClick={() => edit(link)}
                      />
                      <DeleteAction
                        icon={<Trash2 className="size-4" />}
                        confirmLabel="Удалить со статистикой?"
                        onConfirm={() => remove.mutate(link.id)}
                      />
                    </div>
                  </div>
                  {link.url && <code className="block break-all text-xs text-muted">{link.url}</code>}
                  <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                    <div>
                      <dt className="text-muted">Перешло</dt>
                      <dd className="tabular-nums">{number(link.users)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Купили</dt>
                      <dd className="tabular-nums">{number(link.buyers)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Сумма покупок</dt>
                      <dd className="tabular-nums">{rub(link.turnover_rub)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Заработано</dt>
                      <dd className="tabular-nums">
                        {rub(link.earned_rub)}
                        {link.pending_rub > 0 && ` (ждёт ${rub(link.pending_rub)})`}
                      </dd>
                    </div>
                  </dl>
                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-bg px-3 py-2 text-xs">
                    <span>
                      {link.payout_mode === 'balance' ? 'На баланс' : 'Ручная выплата'} · выплачено{' '}
                      {rub(link.paid_out_rub)} · <b>к выплате {rub(link.owed_rub)}</b>
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={link.owed_rub <= 0 || payout.isPending}
                      onClick={() => {
                        if (
                          confirm(
                            `Отметить выплаченным ${rub(link.owed_rub)} для ${link.owner}? Сумма к выплате обнулится.`,
                          )
                        )
                          payout.mutate(link.id)
                      }}
                    >
                      Выплатил
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
