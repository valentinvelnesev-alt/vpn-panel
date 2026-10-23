import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, CheckCircle2, Link2, MessagesSquare, XCircle } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { BotPage } from '@/components/BotShell'
import { SaveBar } from '@/components/page'
import {
  Button,
  Card,
  CopyAction,
  DeleteAction,
  Field,
  Input,
  RowAction,
  Textarea,
} from '@/components/ui'
import { panel, type SupportCheck, type SupportInfo } from '@/lib/api'
import { cn } from '@/lib/cn'

type Mode = 'link' | 'bot'

const STATE_VIEW: Record<SupportInfo['state'], [string, string]> = {
  running: ['bg-success', 'работает'],
  stopped: ['bg-muted', 'остановлена'],
  error: ['bg-danger', 'ошибка'],
}

/** Скриншот к шагу: кладётся в frontend/public/guide/support-N.png и
 *  показывается сам, как только файл появился; нет файла — блока нет. */
function GuideImage({ n }: { n: number }) {
  const [missing, setMissing] = useState(false)
  if (missing) return null
  return (
    <img
      src={`/guide/support-${n}.png`}
      alt={`Шаг ${n}`}
      onError={() => setMissing(true)}
      className="mt-3 max-h-96 rounded-2xl border object-contain"
    />
  )
}

function Step({
  n,
  title,
  done,
  optional,
  children,
}: {
  n: number
  title: string
  done?: boolean
  optional?: boolean
  children: ReactNode
}) {
  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-3">
        <span
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-full text-sm font-semibold',
            done ? 'bg-success/20 text-success' : 'bg-accent/15 text-accent',
          )}
        >
          {done ? <Check className="size-4" /> : n}
        </span>
        <h2 className="font-medium">
          {title}
          {optional && <span className="ml-2 text-xs font-normal text-muted">необязательно</span>}
        </h2>
      </div>
      <div className="space-y-2 text-sm text-muted [&_b]:text-fg [&_code]:rounded [&_code]:bg-bg [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-fg">
        {children}
      </div>
      <GuideImage n={n} />
    </Card>
  )
}

function ModePicker({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  const options: [Mode, string, string, ReactNode][] = [
    [
      'link',
      'Ссылка на @username',
      'Кнопка «Поддержка» открывает чат с человеком или ботом. Самый простой вариант: пара минут.',
      <Link2 key="i" className="size-5" />,
    ],
    [
      'bot',
      'Через бота поддержки',
      'Клиент пишет боту, а вы отвечаете в группе в Telegram. Каждый клиент — своя тема, видно его подписку и баланс.',
      <MessagesSquare key="i" className="size-5" />,
    ],
  ]
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {options.map(([value, title, hint, icon]) => (
        <button
          key={value}
          type="button"
          onClick={() => onChange(value)}
          className={cn(
            'flex flex-col items-start gap-1.5 rounded-3xl border p-4 text-left transition-all',
            mode === value
              ? 'border-accent/60 bg-accent/10 shadow-lg shadow-accent/10'
              : 'border-border/60 bg-surface/40 hover:bg-surface-hover',
          )}
        >
          <span className={cn('flex items-center gap-2 font-medium', mode === value && 'text-accent')}>
            {icon}
            {title}
          </span>
          <span className="text-sm text-muted">{hint}</span>
        </button>
      ))}
    </div>
  )
}

function parseId(raw: string): number | null | undefined {
  const value = raw.trim()
  if (!value) return null
  return /^-?\d+$/.test(value) ? Number(value) : undefined
}

function parseIds(raw: string): number[] | undefined {
  const parts = raw.split(/[\s,;]+/).filter(Boolean)
  if (parts.some((p) => !/^\d+$/.test(p))) return undefined
  return parts.map(Number)
}

function TokenStep({ info }: { info: SupportInfo }) {
  const queryClient = useQueryClient()
  const [token, setToken] = useState('')
  const [replacing, setReplacing] = useState(false)
  const refresh = (data: SupportInfo) => queryClient.setQueryData(['support'], data)

  const save = useMutation({
    mutationFn: () => panel.setSupportToken(token.trim()),
    onSuccess: (data) => {
      refresh(data)
      setToken('')
      setReplacing(false)
    },
  })
  const remove = useMutation({ mutationFn: panel.deleteSupportToken, onSuccess: refresh })
  const connected = !!info.token_masked

  return (
    <Step n={2} title="Создайте бота поддержки" done={connected}>
      <ol className="list-decimal space-y-1 pl-5">
        <li>
          Откройте в Telegram{' '}
          <a
            href="https://t.me/BotFather"
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            @BotFather
          </a>{' '}
          и отправьте ему <code>/newbot</code>.
        </li>
        <li>
          Придумайте название (например, <b>Поддержка</b>) и юзернейм — он должен заканчиваться на{' '}
          <code>bot</code> (например, <code>my_support_bot</code>).
        </li>
        <li>
          BotFather пришлёт <b>токен</b> — длинную строку вида <code>123456:AA…</code>. Скопируйте
          её и вставьте ниже.
        </li>
      </ol>
      <p>
        Это должен быть <b>новый, отдельный бот</b> — не тот, который продаёт подписки.
      </p>

      {connected && !replacing ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-bg/60 px-3 py-2">
          <span className="text-fg">
            <span className="font-medium">@{info.bot_username}</span>
            <span className="text-muted"> · токен {info.token_masked}</span>
          </span>
          <div className="flex gap-1.5">
            <RowAction label="Заменить" onClick={() => setReplacing(true)} icon={null} />
            <DeleteAction
              icon={null}
              label="Отключить"
              confirmLabel="Точно отключить?"
              disabled={remove.isPending}
              onConfirm={() => remove.mutate()}
            />
          </div>
        </div>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
        >
          <Field label="Токен бота поддержки">
            <Input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="123456789:AA..."
              autoComplete="off"
            />
          </Field>
          {save.isError && <p className="text-danger">{(save.error as Error).message}</p>}
          <div className="flex gap-2">
            <Button type="submit" disabled={!token.trim() || save.isPending}>
              {save.isPending ? 'Проверяю…' : 'Подключить'}
            </Button>
            {replacing && (
              <Button type="button" variant="ghost" onClick={() => setReplacing(false)}>
                Отмена
              </Button>
            )}
          </div>
        </form>
      )}
    </Step>
  )
}

function CheckList({ result }: { result: SupportCheck }) {
  return (
    <ul className="space-y-2">
      {result.items.map((item) => (
        <li key={item.key} className="flex items-start gap-2">
          {item.ok ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
          ) : (
            <XCircle className="mt-0.5 size-4 shrink-0 text-danger" />
          )}
          <span>
            <span className={item.ok ? 'text-fg' : 'text-danger'}>{item.title}</span>
            {!item.ok && item.hint && <span className="block text-muted">{item.hint}</span>}
          </span>
        </li>
      ))}
    </ul>
  )
}

function SupportEditor({ info }: { info: SupportInfo }) {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<Mode>(info.mode)
  const [link, setLink] = useState(info.link ?? '')
  const [chat, setChat] = useState(info.chat_id?.toString() ?? '')
  const [admins, setAdmins] = useState(info.admin_ids.join(', '))
  const [welcome, setWelcome] = useState(info.welcome_text ?? '')
  const [savedAt, setSavedAt] = useState<number>()

  const reset = () => {
    setMode(info.mode)
    setLink(info.link ?? '')
    setChat(info.chat_id?.toString() ?? '')
    setAdmins(info.admin_ids.join(', '))
    setWelcome(info.welcome_text ?? '')
  }

  const chatId = parseId(chat)
  const adminIds = parseIds(admins)
  const problem =
    mode !== 'bot'
      ? null
      : !info.token_masked
        ? 'Чтобы включить, подключите бота поддержки (шаг 2)'
        : chatId === undefined || adminIds === undefined
          ? 'ID группы и ID операторов — только цифры'
          : chatId === null
            ? 'Чтобы включить, укажите ID группы (шаг 4)'
            : null
  const invalid = problem !== null
  const dirty =
    mode !== info.mode ||
    link.trim() !== (info.link ?? '') ||
    (chatId ?? null) !== (info.chat_id ?? null) ||
    JSON.stringify([...(adminIds ?? [])].sort((a, b) => a - b)) !== JSON.stringify(info.admin_ids) ||
    welcome.trim() !== (info.welcome_text ?? '')

  const check = useMutation({ mutationFn: panel.checkSupport })
  const test = useMutation({ mutationFn: panel.testSupport })
  const save = useMutation({
    mutationFn: () =>
      panel.saveSupport({
        mode,
        link: link.trim() || null,
        chat_id: chatId ?? null,
        admin_ids: adminIds ?? [],
        welcome_text: welcome.trim() || null,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(['support'], data)
      queryClient.invalidateQueries({ queryKey: ['bot'] })
      setSavedAt(Date.now())
      check.mutate()
    },
  })

  // Проверка запускается сама, как только есть что проверять: шаги ниже
  // отмечаются галочками без нажатия кнопок.
  const autoChecked = useRef('')
  useEffect(() => {
    const key = `${info.token_masked}|${info.chat_id}`
    if (mode === 'bot' && info.token_masked && info.chat_id && autoChecked.current !== key) {
      autoChecked.current = key
      check.mutate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, info.token_masked, info.chat_id])

  const items = Object.fromEntries((check.data?.items ?? []).map((i) => [i.key, i.ok]))
  const [stateColour, stateLabel] = STATE_VIEW[info.state]
  const usable = info.seen_chat && info.seen_chat.id !== info.chat_id

  return (
    <>
      <ModePicker mode={mode} onChange={setMode} />

      {mode === 'link' ? (
        <Card className="space-y-4">
          <Field
            label="Юзернейм или ссылка"
            hint="Например @my_support или https://t.me/my_support. Кнопка «Поддержка» в боте откроет этот чат. Пусто — кнопки не будет."
          >
            <Input
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder="@my_support"
            />
          </Field>
          {info.mode === 'bot' && (
            <p className="text-sm text-muted">
              Сейчас включён бот поддержки — после сохранения он остановится, а кнопка поведёт по
              этой ссылке.
            </p>
          )}
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
            <span className="flex items-center gap-2">
              <span className={cn('size-2 rounded-full', stateColour)} />
              Бот поддержки: {info.mode === 'bot' ? stateLabel : 'ещё не включён'}
            </span>
            {info.mode === 'bot' && (
              <span className="text-muted">
                открытых обращений: {info.open_tickets} · всего: {info.total_tickets}
              </span>
            )}
          </div>
          {info.state === 'error' && info.state_message && (
            <p className="rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger">
              {info.state_message}
            </p>
          )}

          <Step n={1} title="Создайте группу и включите «Темы»" done={items.topics}>
            <ol className="list-decimal space-y-1 pl-5">
              <li>
                В Telegram: <b>Новая группа</b> → назовите её, например, «Поддержка». Добавьте
                любого участника — группу нельзя создать пустой (потом его можно убрать).
              </li>
              <li>
                Откройте группу → нажмите на её название → <b>Изменить</b> (карандаш).
              </li>
              <li>
                Включите переключатель <b>«Темы»</b> (Topics) и сохраните.
              </li>
            </ol>
            <p>
              Так каждый клиент получит в группе свою тему: сообщения не смешиваются, а ответы
              уходят точно тому, кому нужно.
            </p>
          </Step>

          <TokenStep info={info} />

          <Step n={3} title="Добавьте бота в группу администратором" done={items.admin}>
            <ol className="list-decimal space-y-1 pl-5">
              <li>
                Откройте группу → <b>Добавить участника</b> → найдите бота
                {info.bot_username ? (
                  <>
                    {' '}
                    <code>@{info.bot_username}</code>
                  </>
                ) : (
                  ' поддержки (после шага 2)'
                )}
                .
              </li>
              <li>
                Нажмите на бота в списке участников → <b>Сделать администратором</b>.
              </li>
              <li>
                Обязательно включите право <b>«Управление темами»</b> и сохраните.
              </li>
            </ol>
            {info.bot_username && (
              <div>
                <CopyAction text={`@${info.bot_username}`} label="Скопировать юзернейм бота" />
              </div>
            )}
          </Step>

          <Step n={4} title="Укажите ID группы" done={!!info.chat_id && items.chat}>
            <p>
              Это число, которое начинается с <code>-100</code>. Искать его не нужно:
            </p>
            <ul className="list-disc space-y-1 pl-5">
              <li>
                <b>Само:</b> когда вы добавите бота в группу (шаг 3), он напишет туда её ID, а здесь
                появится кнопка «Использовать».
              </li>
              <li>
                <b>Командой:</b> напишите в группе <code>/id</code> — бот ответит ID группы.
              </li>
            </ul>
            {usable && info.seen_chat && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-accent/40 bg-accent/10 px-3 py-2 text-fg">
                <span>
                  Бот увидел группу <b>«{info.seen_chat.title ?? 'без названия'}»</b>
                  <span className="text-muted"> · {info.seen_chat.id}</span>
                </span>
                <Button className="h-9" onClick={() => setChat(String(info.seen_chat!.id))}>
                  Использовать
                </Button>
              </div>
            )}
            <Field label="ID группы">
              <Input
                value={chat}
                onChange={(e) => setChat(e.target.value)}
                placeholder="-1001234567890"
                inputMode="numeric"
              />
            </Field>
            {chatId === undefined && <p className="text-danger">ID — это число, например -1001234567890.</p>}
            {typeof chatId === 'number' && chatId > 0 && (
              <p className="text-danger">
                ID группы начинается с «-100». Похоже, вы указали ID человека — напишите /id в самой
                группе.
              </p>
            )}
          </Step>

          <Step n={5} title="Кто отвечает клиентам" done={info.admin_ids.length > 0} optional>
            <p>
              Отвечать в темах могут <b>все администраторы группы</b> — ничего настраивать не нужно.
              Если хотите разрешить кому-то, кто не администратор, добавьте его ID.
            </p>
            <p>
              Свой ID узнать просто: напишите боту поддержки{' '}
              {info.bot_username ? <code>@{info.bot_username}</code> : 'в личные сообщения'} команду{' '}
              <code>/id</code> — он ответит вашим числом. Или спросите у бота{' '}
              <a
                href="https://t.me/userinfobot"
                target="_blank"
                rel="noreferrer"
                className="text-accent hover:underline"
              >
                @userinfobot
              </a>
              .
            </p>
            <Field label="ID операторов через запятую">
              <Input
                value={admins}
                onChange={(e) => setAdmins(e.target.value)}
                placeholder="123456789, 987654321"
              />
            </Field>
            {adminIds === undefined && <p className="text-danger">Только числа через запятую.</p>}
          </Step>

          <Step n={6} title="Включите и проверьте" done={info.mode === 'bot' && info.state === 'running'}>
            <p>
              Нажмите <b>«Сохранить»</b> внизу экрана — бот поддержки запустится, а кнопка «Поддержка»
              в боте продаж поведёт к нему. Потом проверьте, что всё на месте.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="ghost" disabled={check.isPending} onClick={() => check.mutate()}>
                {check.isPending ? 'Проверяю…' : 'Проверить настройку'}
              </Button>
              <Button
                variant="ghost"
                disabled={test.isPending || !info.token_masked || !info.chat_id}
                onClick={() => test.mutate()}
              >
                Написать в группу
              </Button>
            </div>
            {check.data && <CheckList result={check.data} />}
            {check.isError && <p className="text-danger">{(check.error as Error).message}</p>}
            {test.data && (
              <p className={test.data.ok ? 'text-success' : 'text-danger'}>
                {test.data.title}
                {test.data.hint && <span className="block text-muted">{test.data.hint}</span>}
              </p>
            )}
            <p>
              Потом зайдите в бота продаж → «Поддержка» → напишите тестовое сообщение: в группе
              появится новая тема. Ответьте в ней — ответ придёт вам в бота. Чтобы закрыть
              обращение, напишите в теме <code>/close</code>.
            </p>
          </Step>

          <Card className="space-y-3">
            <h2 className="font-medium">Приветствие бота поддержки</h2>
            <Textarea
              value={welcome}
              onChange={(e) => setWelcome(e.target.value)}
              rows={3}
              placeholder="Здравствуйте! Напишите ваш вопрос одним или несколькими сообщениями — оператор ответит прямо здесь."
            />
            <p className="text-xs text-muted">Показывается клиенту, когда он нажимает «Старт». Пусто — стандартный текст.</p>
          </Card>
        </>
      )}

      <SaveBar
        dirty={dirty}
        pending={save.isPending}
        error={
          save.isError
            ? (save.error as Error).message
            : dirty
              ? problem
              : null
        }
        savedAt={savedAt}
        onSave={() => {
          if (invalid) return
          save.mutate()
        }}
        onReset={reset}
        savedNote={
          mode === 'bot'
            ? 'Сохранено — бот поддержки запускается, перезапускать ничего не нужно'
            : 'Сохранено — кнопка «Поддержка» в боте уже ведёт по этой ссылке'
        }
      />
    </>
  )
}

export default function BotSupportPage() {
  return (
    <BotPage
      title="Поддержка"
      description="Как клиенты обращаются за помощью: ссылка на ваш аккаунт или отдельный бот с диалогами в группе."
      requireToken={false}
    >
      {() => <SupportLoader />}
    </BotPage>
  )
}

function SupportLoader() {
  const { data } = useQuery({
    queryKey: ['support'],
    queryFn: panel.support,
    // Подхватываем «бот увидел группу» и смену состояния без перезагрузки.
    refetchInterval: 5000,
  })
  if (!data) return <p className="text-sm text-muted">Загрузка…</p>
  // key: после смены бота редактор пересоздаётся со свежими значениями.
  return <SupportEditor key={String(data.token_masked)} info={data} />
}
