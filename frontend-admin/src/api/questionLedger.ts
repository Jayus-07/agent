/**
 * 线上问题台账 API（2026-10-08 #13）—— data-explorer「问题收集」tab。
 * 后端 /api/question-ledger（require_admin_user 闸，BFF 注入 X-API-Key +
 * Authorization 转发）。
 */
import { fetchRaw } from './client'

export interface QuestionLedgerItem {
  id: number
  tenant_id: string
  user_id: string
  session_id: string
  domain: string
  question: string
  answer_summary: string
  trace_id: string
  source: string
  status: string
  created_at: string
}

export interface QuestionLedgerPage {
  items: QuestionLedgerItem[]
  total: number
  page: number
  page_size: number
  stats_by_domain?: Record<string, number>
}

export interface ToCandidatesResult {
  converted: { ledger_id: number; candidate_id: string }[]
  skipped: number[]
}

/** 域显示名（与后端 ai.question_ledger 域枚举同口径） */
export const QUESTION_DOMAIN_LABELS: Record<string, string> = {
  travel: '旅游',
  sql: 'SQL',
  planner: '规划',
  cs: '客服',
  rag: 'RAG',
  ai_assistant: 'AI 助手',
}

export const QUESTION_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  accepted: '已入候选',
  dismissed: '已忽略',
}

async function parse<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json() as Promise<T>
}

export const questionLedgerService = {
  list(params: {
    domain?: string
    status?: string
    page?: number
    page_size?: number
  }): Promise<QuestionLedgerPage> {
    const q = new URLSearchParams()
    if (params.domain) q.set('domain', params.domain)
    if (params.status) q.set('status', params.status)
    q.set('page', String(params.page ?? 1))
    q.set('page_size', String(params.page_size ?? 20))
    return fetchRaw(`/api/question-ledger?${q.toString()}`).then(
      (r) => parse<QuestionLedgerPage>(r),)
  },

  setStatus(id: number, status: 'accepted' | 'dismissed') {
    return fetchRaw(`/api/question-ledger/${id}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    }).then((r) => parse<{ ok: boolean }>(r))
  },

  toCandidates(ids: number[]): Promise<ToCandidatesResult> {
    return fetchRaw('/api/question-ledger/to-candidates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    }).then((r) => parse<ToCandidatesResult>(r))
  },
}
