import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Power, RotateCw } from 'lucide-react'
import { Button, Card } from '@/components/ui'
import { panel, type Node } from '@/lib/api'
import { countryFlag } from '@/lib/flag'
import { bytes, dateTime, number, uptime } from '@/lib/format'

function StatusDot({ node }: { node: Node }) {
  const [color, label] = node.disabled
    ? ['bg-muted', 'отключена']
    : node.connecting
      ? ['bg-warning', 'подключается']
      : node.online
        ? ['bg-success', 'на связи']
        : ['bg-danger', 'недоступна']

  return (
    <span className="inline-flex items-center gap-2 text-sm">
      <span className={`size-2 rounded-full ${color}`} aria-hidden />
      {label}
    </span>
  )
}

export default function Nodes() {
  const queryClient = useQueryClient()
  const { data: nodes, isPending, error } = useQuery({
    queryKey: ['nodes'],
    queryFn: panel.nodes,
    refetchInterval: 20_000,
  })

  const action = useMutation({
    mutationFn: ({ uuid, act }: { uuid: string; act: 'enable' | 'disable' | 'restart' }) =>
      panel.nodeAction(uuid, act),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['nodes'] }),
  })

  if (isPending) return <p className="text-sm text-muted">Загрузка…</p>
  if (error) return <p className="text-sm text-danger">{(error as Error).message}</p>

  return (
    <div className="space-y-6">
      {/* Заголовок раздела не дублируем — он уже подсвечен в навигации. */}
      <div className="flex justify-end">
        <span className="text-sm text-muted">
          {nodes.filter((n) => n.online).length} из {nodes.length} на связи
        </span>
      </div>

      {action.isError && (
        <p className="text-sm text-danger">{(action.error as Error).message}</p>
      )}

      {nodes.length === 0 && (
        <Card>
          <p className="text-muted">В Remnawave пока нет ни одной ноды.</p>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {nodes.map((node) => (
          <Card key={node.uuid}>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  {countryFlag(node.country_code) && (
                    <span className="text-base leading-none" title={node.country_code ?? ''}>
                      {countryFlag(node.country_code)}
                    </span>
                  )}
                  <h2 className="truncate font-medium">{node.name}</h2>
                </div>
                <p className="truncate text-sm text-muted">{node.address}</p>
              </div>
              <StatusDot node={node} />
            </div>

            <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
              <div>
                <dt className="text-xs text-muted">Онлайн</dt>
                <dd className="tabular-nums">{number(node.users_online)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Трафик</dt>
                <dd className="tabular-nums">{bytes(node.traffic_used_bytes)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Xray работает</dt>
                <dd className="tabular-nums">{uptime(node.xray_uptime)}</dd>
              </div>
            </dl>

            {!node.online && node.last_status_message && (
              <p className="mt-3 rounded-2xl bg-danger/10 px-3 py-2 text-xs text-danger">
                {node.last_status_message}
                {node.last_status_change &&
                  ` · ${dateTime(node.last_status_change)}`}
              </p>
            )}

            <div className="mt-4 flex items-center gap-2">
              <Button
                variant="ghost"
                onClick={() => action.mutate({ uuid: node.uuid, act: 'restart' })}
                disabled={action.isPending || node.disabled}
              >
                <RotateCw className="size-4" />
                Перезапустить
              </Button>
              <Button
                variant="ghost"
                onClick={() =>
                  action.mutate({
                    uuid: node.uuid,
                    act: node.disabled ? 'enable' : 'disable',
                  })
                }
                disabled={action.isPending}
              >
                <Power className="size-4" />
                {node.disabled ? 'Включить' : 'Отключить'}
              </Button>
              <span className="ml-auto text-xs text-muted">
                {node.xray_version && `Xray ${node.xray_version}`}
              </span>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
