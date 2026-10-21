import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, Play, Square } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '@/components/page'
import { Button, Card } from '@/components/ui'
import { panel, type BotStatus } from '@/lib/api'
import { useBotStatus } from '@/lib/botSettings'
import { cn } from '@/lib/cn'

export function botStateView(status: BotStatus): { colour: string; label: string } {
  if (status.enabled && status.state === 'stopped') {
    return { colour: 'bg-warning animate-pulse', label: 'запускается…' }
  }
  if (status.state === 'running') return { colour: 'bg-success', label: 'работает' }
  if (status.state === 'error') return { colour: 'bg-danger', label: 'ошибка' }
  return { colour: 'bg-muted', label: 'остановлен' }
}

/** «@bot · работает» и кнопка запуска/остановки — видно на каждой странице
 *  бота, не нужно искать, где он включается. */
export function BotStatusChip({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const toggle = useMutation({
    mutationFn: () => (status.enabled ? panel.stopBot() : panel.startBot()),
    onSuccess: (data) => queryClient.setQueryData(['bot'], data),
  })
  if (!status.configured) return null
  const { colour, label } = botStateView(status)

  return (
    <div className="glass flex items-center gap-3 rounded-full py-1.5 pl-4 pr-1.5 text-sm">
      <span className={cn('size-2 rounded-full', colour)} aria-hidden />
      <span>
        {status.bot_username ? `@${status.bot_username}` : 'Бот'}{' '}
        <span className="text-muted">· {label}</span>
      </span>
      <Button
        variant={status.enabled ? 'ghost' : 'primary'}
        className="h-8 px-3"
        disabled={toggle.isPending}
        onClick={() => toggle.mutate()}
      >
        {status.enabled ? <Square className="size-3.5" /> : <Play className="size-3.5" />}
        {status.enabled ? 'Остановить' : 'Запустить'}
      </Button>
    </div>
  )
}

/** Каркас страницы бота: заголовок, статус и подсказка, если бот ещё не
 *  подключён (тогда настраивать остальное бессмысленно). */
export function BotPage({
  title,
  description,
  requireToken = true,
  children,
}: {
  title: string
  description?: ReactNode
  requireToken?: boolean
  children: (status: BotStatus) => ReactNode
}) {
  const { data: status, isPending } = useBotStatus()

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title={title}
        description={description}
        actions={status && <BotStatusChip status={status} />}
      />
      {isPending || !status ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : requireToken && !status.configured ? (
        <Card className="flex flex-wrap items-center gap-4">
          <span className="grid size-11 place-items-center rounded-2xl bg-accent/15 text-accent">
            <Bot className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="font-medium">Сначала подключите бота</p>
            <p className="text-sm text-muted">Вставьте токен из @BotFather — это займёт минуту.</p>
          </div>
          <Link to="/bot">
            <Button>Подключить</Button>
          </Link>
        </Card>
      ) : (
        children(status)
      )}
    </div>
  )
}
