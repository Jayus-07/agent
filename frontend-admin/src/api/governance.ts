/**
 * 治理平面 API（治理台账批次 A/B，2026-09-30）
 *
 * 后端：
 *   - backend/app/api/routes/admin_tools.py      → GET /admin/tools(/stats)（M1/M2）
 *   - backend/app/api/routes/consistency.py      → GET /consistency/report（M6）
 *   - backend/app/api/routes/admin_security.py   → GET /admin/security/events(/stats)（M9）
 *   - backend/app/api/routes/admin_tasks.py      → GET /admin/tasks/queues（M10）
 *   - backend/app/api/routes/observability.py    → GET /observability/tokens/breakdown（M11）
 * 全部为只读接口，前端不做任何写操作。
 */

import { request } from '@/lib/fetcher'

// ── Tool 治理中心（M1/M2） ─────────────────────────────

export interface ToolContractEntry {
  name: string
  module: string
  capabilities: string[]
  output_types: Record<string, string>
  content_hash: string
  description_hash: string
  runtime?: {
    calls?: number
    success?: number
    failures?: number
    success_rate?: number | null
    error_classes?: Record<string, number>
  } | null
}

export interface ToolInventory {
  count: number
  lock_git_sha: string
  lock_error: string
  tools: ToolContractEntry[]
}

export function getToolInventory(): Promise<ToolInventory> {
  return request<ToolInventory>('/api/admin/tools')
}

export interface ToolStats {
  scope: string
  totals: {
    tools_seen: number
    calls: number
    success: number
    failures: number
    success_rate: number | null
  }
  top_failed_tools: {
    tool: string
    failures: number
    calls: number
    error_classes: Record<string, number>
  }[]
  top_error_classes: { error_class: string; count: number }[]
  skill_failures: { skill: string; error_type: string; count: number }[]
  tools: {
    tool: string
    domain: string
    calls: number
    success: number
    failures: number
    success_rate: number | null
    error_classes?: Record<string, number>
  }[]
}

export function getToolStats(): Promise<ToolStats> {
  return request<ToolStats>('/api/admin/tools/stats')
}

// ── 资产一致性中心（M6） ───────────────────────────────

export interface ConsistencySection {
  name: string
  status: 'PASS' | 'FAIL'
  counts: Record<string, number>
  issues: string[]
}

export interface ConsistencyReport {
  generated_at: string
  overall: 'PASS' | 'FAIL'
  failed_sections: string[]
  sections: ConsistencySection[]
}

export function getConsistencyReport(): Promise<ConsistencyReport> {
  return request<ConsistencyReport>('/api/consistency/report')
}

// ── 安全事件（M9） ────────────────────────────────────

export interface SecurityEvent {
  id: number
  ts: string
  event_type: string
  category: string
  user_id: string
  tenant_id: string
  trace_id: string
  session_id: string
  detail: Record<string, unknown>
}

export interface SecurityEventsResponse {
  total: number
  page: number
  page_size: number
  events: SecurityEvent[]
}

export function getSecurityEvents(params: {
  event_type?: string
  user_id?: string
  window_h?: number
  page?: number
  page_size?: number
}): Promise<SecurityEventsResponse> {
  const q = new URLSearchParams()
  if (params.event_type) q.set('event_type', params.event_type)
  if (params.user_id) q.set('user_id', params.user_id)
  q.set('window_h', String(params.window_h ?? 24))
  q.set('page', String(params.page ?? 1))
  q.set('page_size', String(params.page_size ?? 50))
  return request<SecurityEventsResponse>(`/api/admin/security/events?${q.toString()}`)
}

export interface SecurityStats {
  window_h: number
  by_type: Record<string, number>
  by_category: { event_type: string; category: string; count: number }[]
  top_users: { user_id: string; count: number }[]
}

export function getSecurityStats(window_h = 24): Promise<SecurityStats> {
  return request<SecurityStats>(`/api/admin/security/stats?window_h=${window_h}`)
}

// ── 队列 backlog（M10） ────────────────────────────────

export interface QueuesResponse {
  queues: { logical: string; physical: string; waiting: number | null }[]
  workers_online: number
}

export function getTaskQueues(): Promise<QueuesResponse> {
  return request<QueuesResponse>('/api/admin/tasks/queues')
}

// ── 用量六维聚合（M11） ────────────────────────────────

export interface BreakdownRow {
  bucket: string
  calls: number
  total_tokens: number
  cached_tokens: number
  cost_usd: number
}

export interface BreakdownResponse {
  group_by: string
  days: number
  rows: BreakdownRow[]
}

export type BreakdownDim = 'user' | 'tenant' | 'model' | 'skill' | 'tool' | 'domain'

export function getTokenBreakdown(
  group_by: BreakdownDim, days = 7,
): Promise<BreakdownResponse> {
  return request<BreakdownResponse>(
    `/api/observability/tokens/breakdown?group_by=${group_by}&days=${days}`)
}
