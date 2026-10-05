import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  LogOut,
  PanelLeft,
  PanelTop,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { auth, panel } from '@/lib/api'
import { DEFAULT_TITLE } from '@/lib/brand'
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

function BrandCard() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['settings', 'brand'], queryFn: panel.brand })
  const [name, setName] = useState('')
  const [logo, setLogo] = useState('')

  useEffect(() => {
    if (!data) return
    setName(data.brand_name ?? '')
    setLogo(data.brand_logo_url ?? '')
  }, [data])

  const save = useMutation({
    mutationFn: () =>
      panel.saveBrand({
        brand_name: name.trim() || null,
        brand_logo_url: logo.trim() || null,
        hide_powered_by: data?.hide_powered_by ?? false,
      }),
    onSuccess: (saved) => {
      queryClient.setQueryData(['settings', 'brand'], saved)
      queryClient.invalidateQueries({ queryKey: ['brand-public'] })
    },
  })

  return (
    <Card>
      <h2 className="font-medium">Название панели</h2>
      <p className="mt-1 text-sm text-muted">
        Показывается во вкладке браузера, в шапке и на странице входа. По умолчанию —
        нейтральное «{DEFAULT_TITLE}», чтобы по вкладке нельзя было понять, что это за сервис.
      </p>
      <form
        className="mt-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Название">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={DEFAULT_TITLE}
              maxLength={64}
            />
          </Field>
          <Field label="Логотип / иконка вкладки (ссылка)" hint="PNG/SVG по https, необязательно">
            <Input
              value={logo}
              onChange={(e) => setLogo(e.target.value)}
              placeholder="https://…/logo.png"
            />
          </Field>
        </div>
        {save.isError && <p className="text-sm text-danger">{(save.error as Error).message}</p>}
        {save.isSuccess && <p className="text-sm text-success">Сохранено</p>}
        <Button type="submit" disabled={save.isPending}>
          Сохранить
        </Button>
      </form>
    </Card>
  )
}

function SecurityCard() {
  const queryClient = useQueryClient()
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: auth.me })
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')

  const setup = useMutation({ mutationFn: auth.totpSetup })
  const enable = useMutation({
    mutationFn: () => auth.totpEnable(code.trim()),
    onSuccess: (admin) => {
      queryClient.setQueryData(['me'], admin)
      setup.reset()
      setCode('')
    },
  })
  const disable = useMutation({
    mutationFn: () => auth.totpDisable(password, code.trim()),
    onSuccess: (admin) => {
      queryClient.setQueryData(['me'], admin)
      setCode('')
      setPassword('')
    },
  })
  const logoutAll = useMutation({
    mutationFn: auth.logoutAll,
    onSuccess: () => queryClient.setQueryData(['me'], null),
  })

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium">Безопасность</h2>
          <p className="mt-1 text-sm text-muted">
            Двухфакторная аутентификация: кроме пароля при входе нужен код из приложения
            (Google Authenticator, Aegis, 1Password…). После 10 неверных попыток входа IP
            блокируется на 15 минут.
          </p>
        </div>
        {me && (
          <span
            className={cn(
              'shrink-0 rounded-full px-3 py-1 text-xs',
              me.totp_enabled ? 'bg-success/15 text-success' : 'bg-warning/15 text-warning',
            )}
          >
            2FA {me.totp_enabled ? 'включена' : 'выключена'}
          </span>
        )}
      </div>

      {me && !me.totp_enabled && !setup.data && (
        <Button className="mt-4" onClick={() => setup.mutate()} disabled={setup.isPending}>
          <ShieldCheck className="size-4" />
          Включить 2FA
        </Button>
      )}

      {me && !me.totp_enabled && setup.data && (
        <form
          className="mt-4 space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            enable.mutate()
          }}
        >
          <p className="text-sm">
            1. Отсканируйте QR-код в приложении или введите ключ вручную.
          </p>
          <div className="flex flex-wrap items-center gap-4">
            {setup.data.qr_svg && (
              <div
                className="size-40 rounded-2xl bg-white p-2 [&>svg]:h-full [&>svg]:w-full"
                // SVG генерирует наш же бэкенд из otpauth-ссылки.
                dangerouslySetInnerHTML={{ __html: setup.data.qr_svg }}
              />
            )}
            <code className="break-all rounded-xl bg-bg px-3 py-2 text-sm">
              {setup.data.secret}
            </code>
          </div>
          <Field label="2. Код из приложения">
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              inputMode="numeric"
              maxLength={6}
              placeholder="123456"
              autoComplete="one-time-code"
            />
          </Field>
          {enable.isError && <p className="text-sm text-danger">{(enable.error as Error).message}</p>}
          <Button type="submit" disabled={code.length !== 6 || enable.isPending}>
            Подтвердить и включить
          </Button>
        </form>
      )}

      {me?.totp_enabled && (
        <form
          className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
          onSubmit={(e) => {
            e.preventDefault()
            disable.mutate()
          }}
        >
          <Field label="Пароль">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </Field>
          <Field label="Код 2FA">
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              inputMode="numeric"
              maxLength={6}
            />
          </Field>
          <Button
            type="submit"
            variant="danger"
            disabled={!password || code.length !== 6 || disable.isPending}
          >
            Отключить 2FA
          </Button>
          {disable.isError && (
            <p className="text-sm text-danger sm:col-span-3">{(disable.error as Error).message}</p>
          )}
        </form>
      )}

      <hr className="my-5" />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          Завершить все сессии панели — на всех браузерах и устройствах.
        </p>
        <Button variant="ghost" onClick={() => logoutAll.mutate()} disabled={logoutAll.isPending}>
          <LogOut className="size-4" />
          Выйти везде
        </Button>
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

      <SecurityCard />

      <BrandCard />

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
