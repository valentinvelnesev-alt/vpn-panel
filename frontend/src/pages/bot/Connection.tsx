import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, XCircle } from 'lucide-react'
import { useState } from 'react'
import { BotPage } from '@/components/BotShell'
import { SectionTitle } from '@/components/page'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type BotStatus } from '@/lib/api'

function CheckResult({ ok, message }: { ok: boolean; message: string }) {
  return (
    <p className={`flex items-start gap-2 text-sm ${ok ? 'text-success' : 'text-danger'}`}>
      {ok ? (
        <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
      ) : (
        <XCircle className="mt-0.5 size-4 shrink-0" />
      )}
      {message}
    </p>
  )
}

function TokenCard({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const [token, setToken] = useState('')

  const check = useMutation({ mutationFn: () => panel.checkBotToken(token) })
  const save = useMutation({
    mutationFn: () => panel.setBotToken(token),
    onSuccess: (data) => {
      queryClient.setQueryData(['bot'], data)
      setToken('')
      check.reset()
    },
  })
  const start = useMutation({
    mutationFn: panel.startBot,
    onSuccess: (data) => queryClient.setQueryData(['bot'], data),
  })
  const replacing = status.configured

  return (
    <Card className="space-y-4">
      <SectionTitle
        hint={
          <>
            Создайте бота в{' '}
            <a
              href="https://t.me/BotFather"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              @BotFather
            </a>{' '}
            командой /newbot и вставьте сюда его токен.
          </>
        }
      >
        {replacing ? 'Токен бота' : 'Шаг 1. Токен бота'}
      </SectionTitle>

      {status.configured && (
        <div className="rounded-2xl bg-bg/60 px-3 py-2 text-sm">
          <span className="font-medium">@{status.bot_username}</span>
          <span className="text-muted"> · токен {status.token_masked}</span>
        </div>
      )}

      {status.state === 'error' && status.state_message && (
        <p className="rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger">
          {status.state_message}
        </p>
      )}

      {status.configured && !status.enabled && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          <span>Бот подключён, но выключен — клиенты не получают ответов.</span>
          <Button className="h-9" disabled={start.isPending} onClick={() => start.mutate()}>
            Запустить бота
          </Button>
        </div>
      )}

      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <Field
          label={replacing ? 'Заменить на новый токен' : 'Токен'}
          hint={
            replacing
              ? 'Смена бота не удаляет клиентов: они привязаны к Telegram ID. Но новый бот не сможет написать тем, кто его ещё не запускал — предупредите их через старого бота заранее.'
              : 'Токен хранится в базе в зашифрованном виде.'
          }
        >
          <Input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="123456789:AA..."
            autoComplete="off"
          />
        </Field>

        {check.data && <CheckResult ok={check.data.ok} message={check.data.message} />}
        {save.isError && <p className="text-sm text-danger">{(save.error as Error).message}</p>}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!token || save.isPending}>
            {replacing ? 'Заменить токен' : 'Подключить'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => check.mutate()}
            disabled={!token || check.isPending}
          >
            {check.isPending ? 'Проверяю…' : 'Проверить'}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function ProxyCard({ status }: { status: BotStatus }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [host, setHost] = useState('')
  const [port, setPort] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const refresh = (data: BotStatus) => queryClient.setQueryData(['bot'], data)
  const proxy = () => ({
    host: host.trim(),
    port: Number(port),
    username: username.trim() || null,
    password: password || null,
  })
  const valid = host.trim() !== '' && Number(port) > 0 && Number(port) < 65536

  const check = useMutation({ mutationFn: () => panel.checkBotProxy(proxy()) })
  const save = useMutation({
    mutationFn: () => panel.setBotProxy(proxy()),
    onSuccess: (data) => {
      refresh(data)
      setHost('')
      setPort('')
      setUsername('')
      setPassword('')
      setOpen(false)
      check.reset()
    },
  })
  const remove = useMutation({ mutationFn: () => panel.deleteBotProxy(), onSuccess: refresh })

  return (
    <Card className="space-y-4">
      <SectionTitle hint="Нужен, только если сервер в России и Telegram с него не открывается. Через прокси пойдут и бот, и проверки из панели.">
        Прокси для Telegram (необязательно)
      </SectionTitle>

      {status.proxy_masked ? (
        <div className="flex items-center justify-between gap-3 rounded-2xl bg-bg/60 px-3 py-2 text-sm">
          <span>
            Используется <span className="font-medium">{status.proxy_masked}</span>
          </span>
          <div className="flex gap-2">
            {!open && (
              <Button variant="ghost" className="h-9" onClick={() => setOpen(true)}>
                Заменить
              </Button>
            )}
            <Button
              variant="ghost"
              className="h-9"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
            >
              Отключить
            </Button>
          </div>
        </div>
      ) : (
        !open && (
          <Button variant="ghost" onClick={() => setOpen(true)}>
            Указать прокси
          </Button>
        )
      )}

      {open && (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
        >
          <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
            <Field label="Адрес">
              <Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="1.2.3.4" />
            </Field>
            <Field label="Порт">
              <Input
                inputMode="numeric"
                value={port}
                onChange={(e) => setPort(e.target.value.replace(/\D/g, ''))}
                placeholder="1080"
              />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Логин" hint="Если прокси без авторизации — оставьте пустым.">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="off"
              />
            </Field>
            <Field label="Пароль">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
              />
            </Field>
          </div>

          {check.data && <CheckResult ok={check.data.ok} message={check.data.message} />}
          {save.isError && <p className="text-sm text-danger">{(save.error as Error).message}</p>}

          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!valid || save.isPending}>
              {save.isPending ? 'Проверяю…' : 'Сохранить'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => check.mutate()}
              disabled={!valid || check.isPending}
            >
              {check.isPending ? 'Проверяю…' : 'Проверить'}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
          </div>
        </form>
      )}
    </Card>
  )
}

export default function BotConnection() {
  return (
    <BotPage
      title="Подключение бота"
      description="Токен из @BotFather и запуск. Остальные настройки бота — в подпунктах меню слева."
      requireToken={false}
    >
      {(status) => (
        <>
          <TokenCard status={status} />
          <ProxyCard status={status} />
        </>
      )}
    </BotPage>
  )
}
