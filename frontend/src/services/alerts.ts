/**
 * 库存告警 service
 *
 * 后端为 backend/app/api/routes/inventory_alerts.py（前缀 /inventory，
 * 经 next.config.js rewrite 由 /api/inventory 代理）。
 * 注意：/observability/alerts 是另一回事 —— 那是系统降级日志（degradation.jsonl），
 * 无 case id、无状态机，不能用于本页面。
 */

/** 告警工单：库存状态机的一个 case */
export interface AlertCase {
  id: number
  product_id: string
  /** 库存状态：low / out / overstock ... */
  current_state: string
  /** 告警级别：critical / warning / info */
  current_level: string
  /** 工单状态：open / acknowledged / resolved / closed */
  status: string
  resolution_type: string | null
  first_detected_at: string
  last_detected_at?: string
}

/** 按级别聚合的告警计数 */
export interface AlertStats {
  critical: number
  warning: number
  info: number
  resolved: number
}

/** 状态机事件链上的一条事件 */
export interface AlertEvent {
  id: number
  case_id: number
  /** created / upgraded / reminded / resolved / reopened / acknowledged / closed */
  event_type: string
  from_state: string | null
  to_state: string | null
  reason: string[]
  notified: boolean
  created_at: string
}

/** 工单详情：case 本体 + 事件链 */
export interface AlertDetail {
  case: AlertCase
  events: AlertEvent[]
}

/** 列表页 tab 与后端 status 过滤值一一对应（store.list_all_cases 已支持这两个值） */
export type AlertScope = 'active' | 'history'

const BASE = '/api/inventory'

/**
 * 失败时抛错而非静默返回空对象 —— 让调用方能区分"没有告警"和"接口挂了"。
 */
async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`${init?.method ?? 'GET'} ${url} 失败 (${res.status})${detail ? `: ${detail.slice(0, 200)}` : ''}`)
  }
  return res.json() as Promise<T>
}

export const alertService = {
  /** 告警统计（按级别分组） */
  getStats: () => api<{ stats: AlertStats }>(`${BASE}/stats`),

  /** 告警工单列表；scope=active → open+acknowledged，history → resolved+closed */
  getAlerts: (params: { status: AlertScope; page?: number; pageSize?: number }) => {
    const q = new URLSearchParams({
      status: params.status,
      page: String(params.page ?? 1),
      page_size: String(params.pageSize ?? 20),
    })
    return api<{ cases: AlertCase[]; total: number; page: number; page_size: number }>(`${BASE}/cases?${q}`)
  },

  /** 单个工单详情（含事件链） */
  getAlert: (id: number) => api<AlertDetail>(`${BASE}/cases/${id}`),

  /** 状态流转：acknowledged / resolved / closed */
  patchAlert: (id: number, body: { status: string; resolution_type?: string }) =>
    api<{ updated: boolean; case_id: number; status: string }>(`${BASE}/cases/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
}
