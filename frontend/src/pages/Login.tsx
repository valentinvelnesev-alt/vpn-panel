import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Button, Card, Field, Input } from '@/components/ui'
import { auth, ApiError } from '@/lib/api'

export default function Login() {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [needsTotp, setNeedsTotp] = useState(false)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => auth.login(login, password, totp || undefined),
    onSuccess: (admin) => queryClient.setQueryData(['me'], admin),
    onError: (error) => {
      // Второй фактор запрашиваем только когда сервер его действительно ждёт.
      if (error instanceof ApiError && error.message.includes('двухфактор')) {
        setNeedsTotp(true)
      }
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    mutation.mutate()
  }

  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <ShieldCheck className="size-5 text-accent" />
          <h1 className="text-lg font-semibold">Вход в панель</h1>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <Field label="Логин">
            <Input
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </Field>

          <Field label="Пароль">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>

          {needsTotp && (
            <Field label="Код из приложения" hint="6 цифр из Google Authenticator">
              <Input
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                inputMode="numeric"
                maxLength={6}
                autoComplete="one-time-code"
                autoFocus
              />
            </Field>
          )}

          {mutation.isError && (
            <p role="alert" className="text-sm text-danger">
              {(mutation.error as Error).message}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={mutation.isPending}>
            {mutation.isPending ? 'Проверяю…' : 'Войти'}
          </Button>
        </form>
      </Card>
    </div>
  )
}
