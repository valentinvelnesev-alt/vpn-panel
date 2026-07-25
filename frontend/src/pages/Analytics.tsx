import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { BarChart } from '@/components/BarChart'
import { Button, Card } from '@/components/ui'
import { panel } from '@/lib/api'
import { number } from '@/lib/format'

export default function Analytics() {
  const { data } = useQuery({
    queryKey: ['analytics-overview'],
    queryFn: panel.analyticsOverview,
  })

  if (!data) return <p className="text-sm text-muted">Загрузка…</p>

  const revenueTotal = data.revenue_daily.reduce((s, d) => s + d.value, 0)
  const usersTotal = data.new_users_daily.reduce((s, d) => s + d.value, 0)

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <a href={panel.exportPaymentsCsvUrl} download>
          <Button variant="ghost">
            <Download className="size-4" />
            Экспорт платежей CSV
          </Button>
        </a>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-medium">Выручка за 30 дней</h2>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {number(Math.round(revenueTotal))} ₽
          </p>
          <div className="mt-4">
            <BarChart data={data.revenue_daily} formatValue={(v) => `${number(v)} ₽`} />
          </div>
        </Card>

        <Card>
          <h2 className="text-sm font-medium">Новые пользователи за 30 дней</h2>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{number(usersTotal)}</p>
          <div className="mt-4">
            <BarChart data={data.new_users_daily} formatValue={(v) => number(v)} />
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-medium">Конверсия из триала</h2>
          <p className="mt-2 text-3xl font-semibold tabular-nums">
            {data.trial_conversion.rate}%
          </p>
          <p className="mt-1 text-sm text-muted">
            {number(data.trial_conversion.converted)} оплатили из{' '}
            {number(data.trial_conversion.trial_users)} попробовавших триал
          </p>
        </Card>

        <Card>
          <h2 className="text-sm font-medium">Популярные тарифы</h2>
          {data.top_plans.length === 0 ? (
            <p className="mt-3 text-sm text-muted">Покупок пока не было.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {data.top_plans.map((plan) => (
                <li key={plan.title} className="flex items-center justify-between text-sm">
                  <span>{plan.title}</span>
                  <span className="text-muted">
                    {number(plan.purchases)} покупок · {number(Math.round(plan.revenue_rub))} ₽
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}
