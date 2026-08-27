export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }
}

let refreshing: Promise<boolean> | null = null

async function refreshSession(): Promise<boolean> {
  // Параллельные 401 не должны запускать несколько обновлений подряд:
  // ротация refresh-токена гасит предыдущий, и гонка выкинула бы из панели.
  refreshing ??= fetch('/api/v1/auth/refresh', {
    method: 'POST',
    credentials: 'same-origin',
  })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      setTimeout(() => (refreshing = null), 0)
    })
  return refreshing
}

export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, ...rest } = init
  const request: RequestInit = {
    ...rest,
    credentials: 'same-origin',
    headers: {
      ...(json === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...rest.headers,
    },
    ...(json === undefined ? {} : { body: JSON.stringify(json) }),
  }

  let response = await fetch(`/api/v1${path}`, request)

  if (response.status === 401 && path !== '/auth/login' && path !== '/auth/refresh') {
    if (await refreshSession()) {
      response = await fetch(`/api/v1${path}`, request)
    }
  }

  if (!response.ok) {
    const detail = await response
      .json()
      .then((b) => b?.detail)
      .catch(() => null)
    throw new ApiError(response.status, detail ?? `Ошибка ${response.status}`)
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}

export interface Admin {
  id: number
  login: string
  is_owner: boolean
  totp_enabled: boolean
}

export const auth = {
  me: () => api<Admin>('/auth/me'),
  login: (login: string, password: string, totp_code?: string) =>
    api<Admin>('/auth/login', { method: 'POST', json: { login, password, totp_code } }),
  logout: () => api<void>('/auth/logout', { method: 'POST' }),
}

export interface NodeLoad {
  name: string
  country_code: string | null
  users_online: number
  online: boolean
}

export interface Overview {
  configured: boolean
  error: string | null
  users_total: number
  users_active: number
  users_expired: number
  users_limited: number
  users_disabled: number
  online_now: number
  online_last_day: number
  nodes_total: number
  nodes_online: number
  traffic_lifetime_bytes: number
  revenue_daily: DayValue[]
  new_users_daily: DayValue[]
  revenue_total_rub: number
  revenue_today_rub: number
  nodes_load: NodeLoad[]
}

export interface Node {
  uuid: string
  name: string
  address: string
  country_code: string | null
  online: boolean
  disabled: boolean
  connecting: boolean
  users_online: number
  traffic_used_bytes: number
  traffic_limit_bytes: number
  xray_uptime: number
  last_status_message: string | null
  last_status_change: string | null
  xray_version: string | null
  node_version: string | null
}

export interface Squad {
  uuid: string
  name: string
}

export interface PanelUser {
  id: number
  username: string
  status: 'ACTIVE' | 'DISABLED' | 'LIMITED' | 'EXPIRED'
  expire_at: string | null
  telegram_id: number | null
  email: string | null
  tag: string | null
  description: string | null
  hwid_device_limit: number | null
  used_traffic_bytes: number
  traffic_limit_bytes: number
  traffic_limit_strategy: string
  online_at: string | null
  created_at: string | null
  subscription_url: string | null
  squads: Squad[]
}

export interface UserUpdate {
  expire_at?: string
  status?: 'ACTIVE' | 'DISABLED'
  traffic_limit_bytes?: number
  traffic_limit_strategy?: string
  hwid_device_limit?: number
  telegram_id?: number
  email?: string
  tag?: string
  description?: string
  squad_uuids?: string[]
}

export interface UserStatusCounts {
  total: number
  active: number
  expired: number
  limited: number
  disabled: number
}

export interface Device {
  hwid: string
  platform: string | null
  device_model: string | null
  os_version: string | null
  updated_at: string | null
}

export interface BotSettings {
  welcome_text: string | null
  support_url: string | null
  channel_url: string | null
  channel_id: string | null
  require_channel_sub: boolean
  trial_enabled: boolean
  trial_days: number
  trial_squad_uuids: string[]
  trial_hwid_limit: number
  purchase_notify_chat_id: number | null
  admin_telegram_ids: number[]
}

export interface BotStatus extends BotSettings {
  configured: boolean
  enabled: boolean
  state: 'stopped' | 'running' | 'error'
  state_message: string | null
  token_masked: string | null
  bot_username: string | null
  bot_name: string | null
  started_at: string | null
  emoji_mode: 'plain' | 'premium'
  premium_available: boolean
  premium_emoji: Record<string, string>
  node_alerts_enabled: boolean
  node_alerts_chat_id: number | null
}

export interface PlanInput {
  title: string
  days: number
  price_rub: number
  squad_uuids: string[]
  hwid_limit: number
  traffic_limit_bytes: number
  category_id: number | null
  is_active: boolean
  sort_order: number
}

export interface Plan extends PlanInput {
  id: number
}

export interface PlanCategoryInput {
  title: string
  sort_order: number
}

export interface PlanCategory extends PlanCategoryInput {
  id: number
}

export interface Providers {
  platega_enabled: boolean
  platega_merchant_id: string | null
  platega_secret_masked: string | null
  rollypay_enabled: boolean
  rollypay_api_key_masked: string | null
  cryptobot_enabled: boolean
  cryptobot_token_masked: string | null
  stars_enabled: boolean
}

export interface PaymentRow {
  id: number
  telegram_id: number
  username: string | null
  provider: string
  purpose: string
  amount_rub: number
  status: string
  created_at: string
  paid_at: string | null
}

export interface PromoInput {
  code: string
  bonus_days: number
  discount_percent: number
  max_uses: number | null
  expires_at: string | null
  is_active: boolean
}

export interface Promo extends PromoInput {
  id: number
  uses_count: number
}

export interface ReferralSettings {
  referral_enabled: boolean
  referral_reward_days: number
  referral_bonus_days: number
  referral_commission_enabled: boolean
  referral_level1_percent: number
  referral_level2_percent: number
}

export interface ReferralStats {
  total_referred: number
  total_rewards_days: number
  top: { telegram_id: number; username: string | null; invited: number; rewarded_days: number }[]
}

export type Segment = 'all' | 'active' | 'expired' | 'no_purchase'

export interface BroadcastButton {
  text: string
  url: string
}

export interface BroadcastInput {
  text: string
  photo_url: string | null
  buttons: BroadcastButton[]
  segment: Segment
  scheduled_at: string | null
}

export interface BroadcastRow {
  id: number
  text: string
  photo_url: string | null
  buttons: BroadcastButton[]
  segment: Segment
  status: 'scheduled' | 'sending' | 'completed' | 'cancelled'
  scheduled_at: string
  total_recipients: number
  sent_count: number
  failed_count: number
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface DayValue {
  date: string
  value: number
}

export interface AnalyticsOverview {
  revenue_daily: DayValue[]
  new_users_daily: DayValue[]
  trial_conversion: { trial_users: number; converted: number; rate: number }
  top_plans: { title: string; purchases: number; revenue_rub: number }[]
}

export interface RemnawaveSettings {
  url: string | null
  token_masked: string | null
  verify_tls: boolean
  configured: boolean
}

export const panel = {
  overview: () => api<Overview>('/dashboard/overview'),

  nodes: () => api<Node[]>('/nodes'),
  nodeAction: (uuid: string, action: 'enable' | 'disable' | 'restart') =>
    api<void>(`/nodes/${uuid}/${action}`, { method: 'POST' }),
  restartAllNodes: () => api<void>('/nodes/restart-all', { method: 'POST' }),

  users: (params: { start?: number; size?: number; search?: string }) => {
    const query = new URLSearchParams()
    if (params.start) query.set('start', String(params.start))
    if (params.size) query.set('size', String(params.size))
    if (params.search) query.set('search', params.search)
    return api<{ users: PanelUser[]; total: number }>(`/users?${query}`)
  },
  userStatusCounts: () => api<UserStatusCounts>('/users/status-counts'),
  updateUser: (id: number, json: UserUpdate) =>
    api<PanelUser>(`/users/${id}`, { method: 'PATCH', json }),
  extendUser: (id: number, days: number) =>
    api<PanelUser>(`/users/${id}/extend`, { method: 'POST', json: { days } }),
  setUserStatus: (id: number, status: 'ACTIVE' | 'DISABLED') =>
    api<PanelUser>(`/users/${id}/status`, { method: 'POST', json: { status } }),
  devices: (id: number) => api<Device[]>(`/users/${id}/devices`),
  deleteDevice: (id: number, hwid: string) =>
    api<void>(`/users/${id}/devices/${hwid}`, { method: 'DELETE' }),
  resetDevices: (id: number) =>
    api<void>(`/users/${id}/devices`, { method: 'DELETE' }),

  botStatus: () => api<BotStatus>('/bot'),
  checkBotToken: (token: string) =>
    api<{
      ok: boolean
      message: string
      bot_username: string | null
      bot_name: string | null
    }>('/bot/token/check', { method: 'POST', json: { token } }),
  setBotToken: (token: string) =>
    api<BotStatus>('/bot/token', { method: 'PUT', json: { token } }),
  startBot: () => api<BotStatus>('/bot/start', { method: 'POST' }),
  stopBot: () => api<BotStatus>('/bot/stop', { method: 'POST' }),
  saveBotSettings: (json: BotSettings) =>
    api<BotStatus>('/bot/settings', { method: 'PUT', json }),
  setEmojiMode: (json: {
    mode: 'plain' | 'premium'
    premium_emoji: Record<string, string>
    test_chat_id?: number
  }) =>
    api<{ ok: boolean; message: string; status: BotStatus }>('/bot/emoji', {
      method: 'PUT',
      json,
    }),

  plans: () => api<Plan[]>('/bot/plans'),
  createPlan: (json: PlanInput) =>
    api<Plan>('/bot/plans', { method: 'POST', json }),
  updatePlan: (id: number, json: PlanInput) =>
    api<Plan>(`/bot/plans/${id}`, { method: 'PUT', json }),
  deletePlan: (id: number) => api<void>(`/bot/plans/${id}`, { method: 'DELETE' }),
  squads: () => api<{ uuid: string; name: string }[]>('/bot/squads'),

  planCategories: () => api<PlanCategory[]>('/bot/plan-categories'),
  createPlanCategory: (json: PlanCategoryInput) =>
    api<PlanCategory>('/bot/plan-categories', { method: 'POST', json }),
  updatePlanCategory: (id: number, json: PlanCategoryInput) =>
    api<PlanCategory>(`/bot/plan-categories/${id}`, { method: 'PUT', json }),
  deletePlanCategory: (id: number) =>
    api<void>(`/bot/plan-categories/${id}`, { method: 'DELETE' }),

  providers: () => api<Providers>('/payments/providers'),
  savePlatega: (json: { enabled: boolean; merchant_id: string; secret: string }) =>
    api<Providers>('/payments/providers/platega', { method: 'PUT', json }),
  saveRollyPay: (json: { enabled: boolean; api_key: string }) =>
    api<Providers>('/payments/providers/rollypay', { method: 'PUT', json }),
  saveCryptoBot: (json: { enabled: boolean; token: string }) =>
    api<Providers>('/payments/providers/cryptobot', { method: 'PUT', json }),
  saveStars: (json: { enabled: boolean }) =>
    api<Providers>('/payments/providers/stars', { method: 'PUT', json }),
  payments: (limit = 50) => api<PaymentRow[]>(`/payments?limit=${limit}`),

  promoCodes: () => api<Promo[]>('/bot/promo-codes'),
  createPromo: (json: PromoInput) =>
    api<Promo>('/bot/promo-codes', { method: 'POST', json }),
  updatePromo: (id: number, json: PromoInput) =>
    api<Promo>(`/bot/promo-codes/${id}`, { method: 'PATCH', json }),
  deletePromo: (id: number) => api<void>(`/bot/promo-codes/${id}`, { method: 'DELETE' }),

  referralSettings: () => api<ReferralSettings>('/bot/referral'),
  saveReferralSettings: (json: ReferralSettings) =>
    api<ReferralSettings>('/bot/referral', { method: 'PUT', json }),
  referralStats: () => api<ReferralStats>('/bot/referral/stats'),

  broadcasts: () => api<BroadcastRow[]>('/broadcasts'),
  segmentCounts: () => api<Record<Segment, number>>('/broadcasts/segments/counts'),
  createBroadcast: (json: BroadcastInput) =>
    api<BroadcastRow>('/broadcasts', { method: 'POST', json }),
  cancelBroadcast: (id: number) => api<void>(`/broadcasts/${id}`, { method: 'DELETE' }),
  uploadBroadcastPhoto: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/v1/broadcasts/upload-photo', {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    })
    if (!res.ok) {
      const detail = await res.json().catch(() => null)
      throw new ApiError(res.status, detail?.detail ?? `Ошибка ${res.status}`)
    }
    return (await res.json()) as { url: string }
  },

  analyticsOverview: () => api<AnalyticsOverview>('/analytics/overview'),
  exportPaymentsCsvUrl: '/api/v1/analytics/export/payments.csv',

  remnawaveSettings: () => api<RemnawaveSettings>('/settings/remnawave'),
  saveRemnawave: (json: { url: string; token: string; verify_tls: boolean }) =>
    api<RemnawaveSettings>('/settings/remnawave', { method: 'PUT', json }),
  checkRemnawave: (json: { url: string; token: string; verify_tls: boolean }) =>
    api<{ ok: boolean; message: string; version: string | null }>(
      '/settings/remnawave/check',
      { method: 'POST', json },
    ),
}
