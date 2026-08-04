// Service — Alerts API

const BASE = '/api/inventory'

export interface AlertStats {
  critical: number
  warning: number
  info: number
  resolved: number
}

export interface AlertCase {
  id: number
  product_id: string
  current_state: string
  current_level: string
  status: string
  resolution_type: string | null
  first_detected_at: string
  last_detected_at: string
  last_notified_at: string | null
  created_at: string
  updated_at: string
}

export interface AlertEvent {
  id: number
  case_id: number
  event_type: string
  from_state: string | null
  to_state: string | null
  qty: number | null
  stock_days: number | null
  reason: string[]
  notified: boolean
  created_at: string
}

export interface AlertDetail {
  case: AlertCase
  events: AlertEvent[]
}

export interface AlertListResult {
  cases: AlertCase[]
  total: number
  page: number
  page_size: number
}

export const alertService = {
  async getStats(): Promise<{ stats: AlertStats }> {
    const res = await fetch(`${BASE}/stats`)
    return res.json()
  },

  async getAlerts(params?: {
    status?: string; level?: string; page?: number; page_size?: number
  }): Promise<AlertListResult> {
    const qs = new URLSearchParams()
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== '') qs.set(k, String(v))
      })
    }
    const res = await fetch(`${BASE}/cases?${qs}`)
    return res.json()
  },

  async getAlert(caseId: number): Promise<AlertDetail> {
    const res = await fetch(`${BASE}/cases/${caseId}`)
    if (!res.ok) throw new Error(`Alert ${caseId} not found`)
    return res.json()
  },

  async patchAlert(caseId: number, body: {
    status?: string; resolution_type?: string
  }): Promise<{ updated: boolean; case_id: number; status: string }> {
    const res = await fetch(`${BASE}/cases/${caseId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
      throw new Error(err.detail || 'Update failed')
    }
    return res.json()
  },
}
