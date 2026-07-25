import { useQuery } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import AuroraBackground from '@/components/AuroraBackground'
import Layout from '@/components/Layout'
import { auth } from '@/lib/api'
import Analytics from '@/pages/Analytics'
import Bot from '@/pages/Bot'
import Broadcasts from '@/pages/Broadcasts'
import Dashboard from '@/pages/Dashboard'
import Login from '@/pages/Login'
import Nodes from '@/pages/Nodes'
import Payments from '@/pages/Payments'
import Settings from '@/pages/Settings'
import Users from '@/pages/Users'

// Заглушки разделов — наполняются на этапах 2–5.
const Soon = ({ title }: { title: string }) => (
  <div className="space-y-2">
    <h1 className="text-2xl font-semibold">{title}</h1>
    <p className="text-muted">Раздел в разработке.</p>
  </div>
)

export default function App() {
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
          <Route path="bot" element={<Bot />} />
          <Route path="broadcasts" element={<Broadcasts />} />
          <Route path="payments" element={<Payments />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Soon title="Страница не найдена" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
