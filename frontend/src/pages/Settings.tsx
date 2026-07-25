import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, PanelLeft, PanelTop, XCircle } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel } from '@/lib/api'
import { cn } from '@/lib/cn'
import { setNavStyle, useNavStyle, type NavStyle } from '@/lib/navStyle'

function AppearanceCard() {
  const current = useNavStyle()

  const options: { value: NavStyle; label: string; icon: typeof PanelTop }[] = [
    { value: 'compact', label: 'Компактный', icon: PanelTop },
    { value: 'sidebar', label: 'Боковая панель', icon: PanelLeft },
  ]

  return (
    <Card>
      <h2 className="font-medium">Стиль оформления</h2>
      <p className="mt-1 text-sm text-muted">
        Выберите способ навигации по интерфейсу панели.
      </p>

      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {options.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            type="button"
            onClick={() => setNavStyle(value)}
            className={cn(
              'flex items-center justify-center gap-2 rounded-2xl border px-4 py-3 text-sm transition-all',
              current === value
                ? 'border-accent/60 bg-accent/10 font-medium text-accent'
                : 'border-border/60 text-muted hover:bg-surface-hover hover:text-fg',
            )}
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </div>
    </Card>
  )
}

export default function Settings() {
  const queryClient = useQueryClient()
  const { data: current } = useQuery({
    queryKey: ['settings', 'remnawave'],
    queryFn: panel.remnawaveSettings,
  })

  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [verifyTls, setVerifyTls] = useState(true)

  useEffect(() => {
    if (current) {
      setUrl(current.url ?? '')
      setVerifyTls(current.verify_tls)
    }
  }, [current])

  const payload = () => ({ url, token, verify_tls: verifyTls })

  const check = useMutation({ mutationFn: () => panel.checkRemnawave(payload()) })
  const save = useMutation({
    mutationFn: () => panel.saveRemnawave(payload()),
    onSuccess: (data) => {
      queryClient.setQueryData(['settings', 'remnawave'], data)
      queryClient.invalidateQueries({ queryKey: ['overview'] })
      setToken('')
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate()
  }

  return (
    <div className="max-w-2xl space-y-6">

      <AppearanceCard />

      <Card>
        <h2 className="font-medium">Подключение к Remnawave</h2>
        <p className="mt-1 text-sm text-muted">
          Панель берёт из Remnawave пользователей, ноды и статистику. Токен
          создаётся в самой Remnawave, в разделе API-токенов.
        </p>

        <form onSubmit={submit} className="mt-5 space-y-4">
          <Field label="Адрес панели Remnawave" hint="Например: https://panel.example.com">
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://panel.example.com"
              required
            />
          </Field>

          <Field
            label="API-токен"
            hint={
              current?.token_masked
                ? `Сохранён: ${current.token_masked}. Оставьте поле пустым, чтобы не менять.`
                : 'Токен хранится в базе в зашифрованном виде.'
            }
          >
            <Input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={current?.token_masked ? '••••••••' : ''}
              autoComplete="off"
              required={!current?.configured}
            />
          </Field>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={verifyTls}
              onChange={(e) => setVerifyTls(e.target.checked)}
              className="size-4"
            />
            Проверять TLS-сертификат
            <span className="text-xs text-muted">
              (снимите, только если у Remnawave самоподписанный сертификат)
            </span>
          </label>

          {check.data && (
            <p
              className={`flex items-center gap-2 text-sm ${
                check.data.ok ? 'text-success' : 'text-danger'
              }`}
            >
              {check.data.ok ? (
                <CheckCircle2 className="size-4" />
              ) : (
                <XCircle className="size-4" />
              )}
              {check.data.message}
              {check.data.version && ` · версия ${check.data.version}`}
            </p>
          )}
          {save.isError && (
            <p className="text-sm text-danger">{(save.error as Error).message}</p>
          )}
          {save.isSuccess && <p className="text-sm text-success">Сохранено</p>}

          <div className="flex gap-2">
            <Button type="submit" disabled={save.isPending}>
              Сохранить
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => check.mutate()}
              disabled={check.isPending || !url}
            >
              {check.isPending ? 'Проверяю…' : 'Проверить подключение'}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}
