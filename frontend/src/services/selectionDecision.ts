/**
 * 选品决策 service
 *
 * 后端为 backend/app/api/routes/selection_decision.py（前缀 /selection-decision，
 * 经 next.config.js rewrite 代理）。相对路径 + request()。
 */
import { request } from '@/lib/fetcher'

const BASE = '/selection-decision'

export interface FinanceParams {
  sell_price: number
  unit_cost: number
  platform_fee_rate?: number
  shipping_cost?: number
  marketing_cost?: number
  monthly_fixed_cost?: number
  min_margin_rate?: number
  initial_inventory?: number
  buffer_rate?: number
}

export interface TaskPayload {
  category: string
  platforms: string[]
  finance: FinanceParams
  panel_size: number
}

export interface SelectionTask {
  id: string
  status: string        // running / success / failed / partial
  verdict: string | null
  trace_id: string
  error: string | null
  created_at: string
  finished_at: string | null
  inputs: { category: string; platforms: string[] }
}

export interface SelectionTaskDetail extends SelectionTask {
  report_md: string | null
}

export const selectionDecisionApi = {
  submit(payload: TaskPayload) {
    return request<{ task_id: string; status: string }>(`${BASE}/tasks`, {
      method: 'POST', body: JSON.stringify(payload), timeout: 30_000,
    })
  },
  list(page = 1, pageSize = 20) {
    return request<{ tasks: SelectionTask[] }>(`${BASE}/tasks?page=${page}&page_size=${pageSize}`)
  },
  get(id: string) {
    return request<SelectionTaskDetail>(`${BASE}/tasks/${id}`)
  },
}
