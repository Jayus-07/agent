/**
 * 选品决策 service
 *
 * 后端为 backend/app/api/routes/selection_decision.py（前缀 /selection-decision，
 * 经 next.config.js rewrite 代理）。相对路径 + request()。
 */
import { request } from '@/lib/fetcher'

const BASE = '/api/selection-decision'

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
  trace_id: string | null
  error: string | null
  created_at: string
  finished_at: string | null
  inputs: { category: string; platforms: string[] }
}

export interface SelectionTaskDetail extends SelectionTask {
  report_md: string | null
}

/** decision_log 行（B1 拍板闭环，2026-09-17） */
export interface DecisionRecord {
  decision_id: string
  task_id: string | null
  candidate_id: string
  category: string | null
  decision_version: number
  evidence_snapshot: Record<string, unknown>
  score_snapshot: Record<string, unknown>
  recommendation: string
  user_decision: 'adopted' | 'rejected' | 'deferred' | null
  decision_at: string
  actual_metrics: Record<string, unknown> | null
  feedback_at: string | null
}

export type UserDecision = 'adopted' | 'rejected' | 'deferred'

export interface DecidePayload {
  candidate_id: string
  decision: UserDecision
  category?: string | null
  recommendation?: string | null
  evidence_snapshot?: Record<string, unknown>
  score_snapshot?: Record<string, unknown>
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
  /** B1：任务下决策留痕列表（拍板时间倒序） */
  listDecisions(taskId: string) {
    return request<{ decisions: DecisionRecord[] }>(`${BASE}/tasks/${taskId}/decisions`)
  },
  /** B1：拍板（留痕 + 用户决策一步完成，幂等由 decision_version 递增表达） */
  decide(taskId: string, payload: DecidePayload) {
    return request<DecisionRecord>(`${BASE}/tasks/${taskId}/decisions`, {
      method: 'POST', body: JSON.stringify(payload), timeout: 15_000,
    })
  },
  /** B1：事后真实表现回填（销量/评价/收益等自由键值） */
  feedback(decisionId: string, actualMetrics: Record<string, unknown>) {
    return request<DecisionRecord>(`${BASE}/decisions/${decisionId}/feedback`, {
      method: 'POST', body: JSON.stringify({ actual_metrics: actualMetrics }), timeout: 15_000,
    })
  },
}
