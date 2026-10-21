import { useQuery } from '@tanstack/react-query'
import { lazy } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import AuroraBackground from '@/components/AuroraBackground'
import Layout from '@/components/Layout'
import { auth } from '@/lib/api'
import { useBrand } from '@/lib/brand'
import Login from '@/pages/Login'

// Разделы грузятся по мере открытия: панель приходит в браузер не одним
// файлом на полмегабайта, а маленькими кусками — первый вход быстрее.
const Analytics = lazy(() => import('@/pages/Analytics'))
const BotBonuses = lazy(() => import('@/pages/bot/Bonuses'))
const BotConnection = lazy(() => import('@/pages/bot/Connection'))
const BotMenu = lazy(() => import('@/pages/bot/Menu'))
const BotNotifications = lazy(() => import('@/pages/bot/Notifications'))
const BotPlansPage = lazy(() => import('@/pages/bot/Plans'))
const BotTrial = lazy(() => import('@/pages/bot/Trial'))
const Broadcasts = lazy(() => import('@/pages/Broadcasts'))
const Clients = lazy(() => import('@/pages/Clients'))
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Nodes = lazy(() => import('@/pages/Nodes'))
const Payments = lazy(() => import('@/pages/Payments'))
const SettingsConnection = lazy(() => import('@/pages/Settings'))
const SettingsAppearance = lazy(() =>
  import('@/pages/Settings').then((m) => ({ default: m.SettingsAppearance })),
)
const SettingsImport = lazy(() =>
  import('@/pages/Settings').then((m) => ({ default: m.SettingsImport })),
)
const SettingsSecurity = lazy(() =>
  import('@/pages/Settings').then((m) => ({ default: m.SettingsSecurity })),
)
const SettingsBackups = lazy(() => import('@/pages/SettingsBackups'))
const Users = lazy(() => import('@/pages/Users'))

// Заглушки разделов — наполняются на этапах 2–5.
const Soon = ({ title }: { title: string }) => (
  <div className="space-y-2">
    <h1 className="text-2xl font-semibold">{title}</h1>
    <p className="text-muted">Раздел в разработке.</p>
  </div>
)

export default function App() {
  useBrand()
  const { data: admin, isPending } = useQuery({
    queryKey: ['me'],
    queryFn: auth.me,
    retry: false,
    // Сессию проверяем один раз на загрузку; дальше её поддерживает
    // автоматическое обновление токена в api().
    staleTime: Infinity,
  })

  if (isPending) {
    return (
      <>
        <AuroraBackground />
        <div className="grid min-h-dvh place-items-center text-sm text-muted">
          Загрузка…
        </div>
      </>
    )
  }

  if (!admin) {
    return (
      <>
        <AuroraBackground />
        <Login />
      </>
    )
  }

  return (
    <BrowserRouter>
      <AuroraBackground />
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="nodes" element={<Nodes />} />
          <Route path="users" element={<Users />} />
          <Route path="clients" element={<Clients />} />
          <Route path="bot" element={<BotConnection />} />
          <Route path="bot/menu" element={<BotMenu />} />
          <Route path="bot/plans" element={<BotPlansPage />} />
          <Route path="bot/trial" element={<BotTrial />} />
          <Route path="bot/bonuses" element={<BotBonuses />} />
          <Route path="bot/notifications" element={<BotNotifications />} />
          <Route path="broadcasts" element={<Broadcasts />} />
          <Route path="payments" element={<Payments />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="settings" element={<SettingsConnection />} />
          <Route path="settings/security" element={<SettingsSecurity />} />
          <Route path="settings/appearance" element={<SettingsAppearance />} />
          <Route path="settings/backups" element={<SettingsBackups />} />
          <Route path="settings/import" element={<SettingsImport />} />
          <Route path="*" element={<Soon title="Страница не найдена" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
