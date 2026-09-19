// Keyword rule service — 对接词库管理 API（/rag/keywords）
// eslint-disable
import { fetchRaw } from '@/api/client'
export interface KeywordRule {
  id: number | string
  keyword: string
  doc_type: string
  category: string
  weight: number
  enabled: number
  source: string
  created_at: string
  updated_at: string
  [key: string]: any
}

export interface RuleMutationResult {
  status: 'draft' | 'published' | 'rolled_back' | 'legacy'
  version: number | null
  rules_hash: string
  taxonomy_version: string
  review_required: boolean
  reason?: string
}

export interface ActiveRuleVersion extends RuleMutationResult {
  effective_at?: string | null
}

export interface RuleVersionSummary extends RuleMutationResult {
  actor?: string
  approval_id?: string
  approved_by?: string
  effective_at?: string | null
  created_at?: string | null
}

export interface KeywordListParams {
  doc_type?: string
  category?: string
  search?: string
  enabled?: string // '' 全部 / '1' 启用 / '0' 停用
}

const BASE = '/api/rag/keywords'

async function parse<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail
    throw new Error(typeof detail === 'string' ? detail : `请求失败（HTTP ${res.status}）`)
  }
  return body as T
}

const qs = (params: Record<string, any>) => {
  const clean: Record<string, string> = {}
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') clean[k] = String(v)
  }
  return new URLSearchParams(clean).toString()
}

export const keywordService = {
  list: (params: KeywordListParams = {}): Promise<{ items: KeywordRule[] }> =>
    fetchRaw(`${BASE}?${qs(params)}`).then(r => r.json()).catch(() => ({ items: [] })),

  docTypes: (): Promise<{ doc_types: string[] }> =>
    fetchRaw(`${BASE}/doc-types`).then(r => r.json()).catch(() => ({ doc_types: [] })),

  categories: (): Promise<{ categories: string[] }> =>
    fetchRaw(`${BASE}/categories`).then(r => r.json()).catch(() => ({ categories: [] })),

  activeVersion: (): Promise<ActiveRuleVersion> =>
    fetchRaw(`${BASE}/versions/active`).then(parse<ActiveRuleVersion>),

  versions: (): Promise<{ items: RuleVersionSummary[] }> =>
    fetchRaw(`${BASE}/versions`).then(parse<{ items: RuleVersionSummary[] }>),

  upsert: (rule: { keyword: string; doc_type: string; category?: string; weight?: number; enabled?: number }, reason: string): Promise<RuleMutationResult> =>
    fetchRaw(BASE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...rule, reason }),
    }).then(parse<RuleMutationResult>),

  batchUpsert: (items: Array<{ keyword: string; doc_type: string; category?: string; weight?: number }>, reason: string, remove_keywords: string[] = []): Promise<RuleMutationResult> =>
    fetchRaw(`${BASE}/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items, remove_keywords, reason }),
    }).then(parse<RuleMutationResult>),

  publish: (version: number, approval_id: string, reason: string): Promise<RuleMutationResult> =>
    fetchRaw(`${BASE}/versions/${version}/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id, reason }),
    }).then(parse<RuleMutationResult>),

  rollback: (version: number, reason: string): Promise<RuleMutationResult> =>
    fetchRaw(`${BASE}/versions/${version}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    }).then(parse<RuleMutationResult>),

  delete: (keyword: string, reason: string): Promise<RuleMutationResult> =>
    fetchRaw(`${BASE}/${encodeURIComponent(keyword)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    }).then(parse<RuleMutationResult>),

  toggle: (keyword: string, enabled: number, reason: string): Promise<RuleMutationResult> =>
    fetchRaw(`${BASE}/${encodeURIComponent(keyword)}/toggle`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled, reason }),
    }).then(parse<RuleMutationResult>),
}
