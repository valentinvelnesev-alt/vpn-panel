import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ImagePlus, Send, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type BroadcastButton, type BroadcastRow, type Segment } from '@/lib/api'
import { dateTime, number } from '@/lib/format'

const SEGMENT_LABEL: Record<Segment, string> = {
  all: 'Все пользователи',
  active: 'Активная подписка',
  expired: 'Подписка истекла',
  no_purchase: 'Ни разу не платили',
}

const STATUS_LABEL: Record<BroadcastRow['status'], string> = {
  scheduled: 'запланирована',
  sending: 'отправляется',
  completed: 'завершена',
  cancelled: 'отменена',
}

function useBroadcastProgress(broadcast: BroadcastRow) {
  const [live, setLive] = useState(broadcast)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    setLive(broadcast)
    if (broadcast.status !== 'sending' && broadcast.status !== 'scheduled') return

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/api/v1/broadcasts/${broadcast.id}/ws`)
    wsRef.current = ws
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (!data.error) setLive(data)
    }
    return () => ws.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [broadcast.id, broadcast.status])

  return live
}

function BroadcastCard({
  broadcast,
  onCancel,
}: {
  broadcast: BroadcastRow
  onCancel: (id: number) => void
}) {
  const live = useBroadcastProgress(broadcast)
  const progress =
    live.total_recipients > 0
      ? Math.round(((live.sent_count + live.failed_count) / live.total_recipients) * 100)
      : 0

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate whitespace-pre-wrap text-sm">{live.text}</p>
          <p className="mt-1 text-xs text-muted">
            {SEGMENT_LABEL[live.segment]} · {STATUS_LABEL[live.status]} ·{' '}
            {dateTime(live.scheduled_at)}
          </p>
        </div>
        {live.status === 'scheduled' && (
          <Button variant="ghost" className="h-8 px-2 text-danger" onClick={() => onCancel(live.id)}>
            <Trash2 className="size-4" />
          </Button>
        )}
      </div>

      {(live.status === 'sending' || live.status === 'completed') && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-bg">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="mt-1.5 text-xs text-muted">
            {number(live.sent_count)} доставлено
            {live.failed_count > 0 && ` · ${number(live.failed_count)} не доставлено`}
            {' · из '}
            {number(live.total_recipients)}
          </p>
        </div>
      )}
    </Card>
  )
}

export default function Broadcasts() {
  const queryClient = useQueryClient()
  const [text, setText] = useState('')
  const [photoUrl, setPhotoUrl] = useState<string | null>(null)
  const [photoPreview, setPhotoPreview] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [segment, setSegment] = useState<Segment>('all')
  const [buttons, setButtons] = useState<BroadcastButton[]>([])
  const [scheduleLater, setScheduleLater] = useState(false)
  const [scheduledAt, setScheduledAt] = useState('')

  const { data: counts } = useQuery({
    queryKey: ['segment-counts'],
    queryFn: panel.segmentCounts,
  })
  const { data: broadcasts } = useQuery({
    queryKey: ['broadcasts'],
    queryFn: panel.broadcasts,
    refetchInterval: 5000,
  })

  const upload = useMutation({
    mutationFn: (file: File) => panel.uploadBroadcastPhoto(file),
    onSuccess: (data, file) => {
      setPhotoUrl(data.url)
      setPhotoPreview(URL.createObjectURL(file))
    },
  })

  const create = useMutation({
    mutationFn: () =>
      panel.createBroadcast({
        text,
        photo_url: photoUrl,
        buttons: buttons.filter((b) => b.text && b.url),
        segment,
        scheduled_at: scheduleLater && scheduledAt ? new Date(scheduledAt).toISOString() : null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['broadcasts'] })
      setText('')
      setPhotoUrl(null)
      setPhotoPreview(null)
      setButtons([])
      setScheduleLater(false)
      setScheduledAt('')
    },
  })

  const cancel = useMutation({
    mutationFn: (id: number) => panel.cancelBroadcast(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['broadcasts'] }),
  })

  return (
    <div className="max-w-3xl space-y-6">

      <Card>
        <h2 className="font-medium">Новая рассылка</h2>
        <form
          className="mt-4 space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate()
          }}
        >
          <Field label="Текст">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              className="w-full rounded-2xl border bg-bg p-3 text-sm"
              placeholder="Поддерживается HTML-разметка Telegram: <b>, <i>, <a>…"
              required
            />
          </Field>

          <div>
            <span className="text-sm font-medium">Фото (необязательно)</span>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) upload.mutate(file)
                e.target.value = ''
              }}
            />
            {photoPreview ? (
              <div className="mt-2 flex items-center gap-3">
                <img
                  src={photoPreview}
                  alt=""
                  className="h-16 w-16 rounded-2xl object-cover"
                />
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setPhotoUrl(null)
                    setPhotoPreview(null)
                  }}
                >
                  <X className="size-4" />
                  Убрать
                </Button>
              </div>
            ) : (
              <Button
                type="button"
                variant="ghost"
                className="mt-2"
                onClick={() => fileInputRef.current?.click()}
                disabled={upload.isPending}
              >
                <ImagePlus className="size-4" />
                {upload.isPending ? 'Загрузка…' : 'Прикрепить фото'}
              </Button>
            )}
            {upload.isError && (
              <p className="mt-1 text-sm text-danger">{(upload.error as Error).message}</p>
            )}
          </div>

          <div>
            <span className="text-sm font-medium">Кнопки (необязательно)</span>
            <div className="mt-2 space-y-2">
              {buttons.map((button, i) => (
                <div key={i} className="flex gap-2">
                  <Input
                    className="flex-1"
                    placeholder="Текст кнопки"
                    value={button.text}
                    onChange={(e) => {
                      const next = [...buttons]
                      next[i] = { ...next[i], text: e.target.value }
                      setButtons(next)
                    }}
                  />
                  <Input
                    className="flex-[2]"
                    placeholder="https://..."
                    value={button.url}
                    onChange={(e) => {
                      const next = [...buttons]
                      next[i] = { ...next[i], url: e.target.value }
                      setButtons(next)
                    }}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setButtons(buttons.filter((_, j) => j !== i))}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
              {buttons.length < 8 && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setButtons([...buttons, { text: '', url: '' }])}
                >
                  Добавить кнопку
                </Button>
              )}
            </div>
          </div>

          <Field label="Кому">
            <select
              value={segment}
              onChange={(e) => setSegment(e.target.value as Segment)}
              className="h-10 w-full rounded-2xl border bg-bg px-3 text-sm"
            >
              {(Object.keys(SEGMENT_LABEL) as Segment[]).map((s) => (
                <option key={s} value={s}>
                  {SEGMENT_LABEL[s]} {counts ? `(${number(counts[s])})` : ''}
                </option>
              ))}
            </select>
          </Field>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={scheduleLater}
              onChange={(e) => setScheduleLater(e.target.checked)}
            />
            Запланировать на другое время
          </label>
          {scheduleLater && (
            <Field label="Дата и время отправки">
              <Input
                type="datetime-local"
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
                required
              />
            </Field>
          )}

          {create.isError && (
            <p className="text-sm text-danger">{(create.error as Error).message}</p>
          )}

          <Button type="submit" disabled={create.isPending || !text}>
            <Send className="size-4" />
            {scheduleLater ? 'Запланировать' : 'Отправить сейчас'}
          </Button>
        </form>
      </Card>

      <div className="space-y-3">
        {broadcasts?.length === 0 && (
          <Card>
            <p className="text-sm text-muted">Рассылок пока не было.</p>
          </Card>
        )}
        {broadcasts?.map((b) => (
          <BroadcastCard key={b.id} broadcast={b} onCancel={cancel.mutate} />
        ))}
      </div>
    </div>
  )
}
