import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BotPage } from '@/components/BotShell'
import { Toggle } from '@/components/page'
import { Card } from '@/components/ui'
import { panel, type BotStatus } from '@/lib/api'
import BotPlans from '../BotPlans'
import BotTraffic from '../BotTraffic'

function MultipleSubscriptions({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const save = useMutation({
    mutationFn: (value: boolean) =>
      panel.saveBotSettings({ ...status, allow_multiple_subscriptions: value }),
    onSuccess: (data) => queryClient.setQueryData(['bot'], data),
  })
  return (
    <Card className="space-y-2">
      <Toggle
        checked={status.allow_multiple_subscriptions}
        disabled={save.isPending}
        onChange={(value) => save.mutate(value)}
        label="Несколько подписок на одного клиента"
        hint="Выключено: у кого есть подписка, видит «Продлить», и оплата продлевает её — ссылка не меняется. Включено: появляется ещё «Купить ещё одну» — отдельный ключ. Применяется сразу."
      />
      {save.isError && <p className="text-sm text-danger">{(save.error as Error).message}</p>}
    </Card>
  )
}

export default function BotPlansPage() {
  return (
    <BotPage
      title="Тарифы"
      description="Что и за сколько продаёт бот: тарифы, общая скидка и пакеты трафика. Изменения видны клиентам сразу."
    >
      {(status) => (
        <>
          <BotPlans />
          <MultipleSubscriptions status={status} />
          <BotTraffic />
        </>
      )}
    </BotPage>
  )
}
