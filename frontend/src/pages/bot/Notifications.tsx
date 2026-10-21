import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BotPage } from '@/components/BotShell'
import { SaveBar, SectionTitle, Toggle } from '@/components/page'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type BotStatus } from '@/lib/api'
import { useBotSettingsDraft } from '@/lib/botSettings'

const parseIds = (raw: string) =>
  raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n))

function NotificationsEditor({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const draft = useBotSettingsDraft(status)
  const { set } = draft

  // Поля ввода держим строками: «-100…» и «@группа» не числа, пока их не
  // проверили, а список ID удобнее править как текст.
  const [chat, setChat] = useState('')
  const [admins, setAdmins] = useState('')
  const [alertsOn, setAlertsOn] = useState(status.node_alerts_enabled)
  const [alertsChat, setAlertsChat] = useState('')
  useEffect(() => {
    setChat(status.purchase_notify_chat_id?.toString() ?? '')
    setAdmins(status.admin_telegram_ids.join(', '))
    setAlertsOn(status.node_alerts_enabled)
    setAlertsChat(status.node_alerts_chat_id?.toString() ?? '')
  }, [status])

  useEffect(() => {
    const raw = chat.trim()
    set('purchase_notify_chat_id', /^-?\d+$/.test(raw) ? Number(raw) : null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat])
  useEffect(() => {
    set('admin_telegram_ids', parseIds(admins))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [admins])

  const alertsChatId = /^-?\d+$/.test(alertsChat.trim()) ? Number(alertsChat.trim()) : null
  const alertsDirty =
    alertsOn !== status.node_alerts_enabled ||
    alertsChatId !== (status.node_alerts_chat_id ?? null)
  const chatInvalid = chat.trim() !== '' && !/^-?\d+$/.test(chat.trim())
  const chatDirty = chat.trim() !== (status.purchase_notify_chat_id?.toString() ?? '')

  const testChat = useMutation({
    mutationFn: () => panel.testNotifyChat(chat.trim()),
    onSuccess: (data) => {
      // @имя группы превращаем в числовой id — в настройке хранится он.
      if (data.ok && data.chat_id !== null) setChat(String(data.chat_id))
    },
  })

  const [savedAt, setSavedAt] = useState<number>()
  const save = useMutation({
    mutationFn: async () => {
      if (chatInvalid) {
        throw new Error('Нажмите «Проверить» у чата продаж — панель подставит числовой id группы')
      }
      if (alertsOn && alertsChatId === null) {
        throw new Error('Укажите числовой ID чата для алертов о нодах')
      }
      let latest = status
      if (draft.dirty) latest = await panel.saveBotSettings(draft.form)
      if (alertsDirty) {
        latest = await panel.saveNodeAlerts({ enabled: alertsOn, chat_id: alertsChatId })
      }
      return latest
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['bot'], data)
      setSavedAt(Date.now())
    },
  })

  const reset = () => {
    save.reset()
    draft.reset()
    setChat(status.purchase_notify_chat_id?.toString() ?? '')
    setAdmins(status.admin_telegram_ids.join(', '))
    setAlertsOn(status.node_alerts_enabled)
    setAlertsChat(status.node_alerts_chat_id?.toString() ?? '')
  }

  return (
    <>
      <Card className="space-y-4">
        <SectionTitle hint="Покупки, продления и автопродления — сообщением в вашу группу. Добавьте бота в группу и нажмите «Проверить».">
          Уведомления о продажах
        </SectionTitle>
        <Field
          label="ID группы или @имя"
          hint="ID целиком, с «-100» в начале (например -1001234567890). Пусто — уведомления выключены."
        >
          <div className="flex gap-2">
            <Input
              placeholder="-1001234567890 или @mygroup"
              value={chat}
              onChange={(e) => {
                setChat(e.target.value)
                testChat.reset()
              }}
            />
            <Button
              type="button"
              variant="ghost"
              onClick={() => testChat.mutate()}
              disabled={!chat.trim() || testChat.isPending}
            >
              {testChat.isPending ? 'Проверяю…' : 'Проверить'}
            </Button>
          </div>
        </Field>
        {testChat.data && (
          <p
            className={`flex items-start gap-2 text-sm ${
              testChat.data.ok ? 'text-success' : 'text-danger'
            }`}
          >
            {testChat.data.ok ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            ) : (
              <XCircle className="mt-0.5 size-4 shrink-0" />
            )}
            {testChat.data.message}
          </p>
        )}
        {testChat.isError && (
          <p className="text-sm text-danger">{(testChat.error as Error).message}</p>
        )}
      </Card>

      <Card className="space-y-4">
        <SectionTitle hint="Им в боте открывается команда /admin: статистика, рассылка и промокоды прямо с телефона. Сюда же приходят тестовые рассылки. Свой ID — у @userinfobot.">
          Администраторы бота
        </SectionTitle>
        <Field label="Telegram ID через запятую">
          <Input
            value={admins}
            onChange={(e) => setAdmins(e.target.value)}
            placeholder="123456789, 987654321"
          />
        </Field>
      </Card>

      <Card className="space-y-4">
        <SectionTitle hint="Бот напишет, если нода перестала отвечать, и когда она снова на связи.">
          Алерты о нодах
        </SectionTitle>
        <Toggle checked={alertsOn} onChange={setAlertsOn} label="Сообщать о падении нод" />
        {alertsOn && (
          <Field label="ID чата для алертов" hint="Ваш Telegram ID или ID группы с «-100».">
            <Input
              value={alertsChat}
              onChange={(e) => setAlertsChat(e.target.value)}
              placeholder="123456789"
            />
          </Field>
        )}
      </Card>

      <SaveBar
        dirty={draft.dirty || alertsDirty || chatDirty}
        pending={save.isPending}
        error={save.isError ? (save.error as Error).message : null}
        savedAt={savedAt}
        onSave={() => save.mutate()}
        onReset={reset}
      />
    </>
  )
}

export default function BotNotifications() {
  return (
    <BotPage
      title="Уведомления и админы"
      description="Куда бот сообщает о продажах и сбоях и кому открыта команда /admin."
    >
      {(status) => <NotificationsEditor status={status} />}
    </BotPage>
  )
}
