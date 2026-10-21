import { useQuery } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import AuroraBackground from '@/components/AuroraBackground'
import Layout from '@/components/Layout'
import { auth } from '@/lib/api'
import Analytics from '@/pages/Analytics'
import BotBonuses from '@/pages/bot/Bonuses'
import BotConnection from '@/pages/bot/Connection'
import BotMenu from '@/pages/bot/Menu'
import BotNotifications from '@/pages/bot/Notifications'
import BotPlansPage from '@/pages/bot/Plans'
import BotTrial from '@/pages/bot/Trial'
import { useBrand } from '@/lib/brand'
import Broadcasts from '@/pages/Broadcasts'
import Clients from '@/pages/Clients'
import Dashboard from '@/pages/Dashboard'
import Login from '@/pages/Login'
import Nodes from '@/pages/Nodes'
import Payments from '@/pages/Payments'
import SettingsConnection, {
  SettingsAppearance,
  SettingsImport,
  SettingsSecurity,
} from '@/pages/Settings'
import SettingsBackups from '@/pages/SettingsBackups'
import Users from '@/pages/Users'

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
