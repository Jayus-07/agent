// Keyword rule service — 对接词库管理 API（/rag/keywords）
// eslint-disable
import { fetchRaw } from '@/api/client'
export interface KeywordRule {
  id: number
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

export interface KeywordListParams {
  doc_type?: string
  category?: string
  search?: string
  enabled?: string // '' 全部 / '1' 启用 / '0' 停用
}

const BASE = '/api/rag/keywords'

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

  upsert: (rule: { keyword: string; doc_type: string; category?: string; weight?: number; enabled?: number }) =>
    fetchRaw(BASE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rule),
    }).then(r => r.json()).catch(() => ({ ok: false })),

  batchUpsert: (items: Array<{ keyword: string; doc_type: string; category?: string; weight?: number }>) =>
    fetchRaw(`${BASE}/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    }).then(r => r.json()).catch(() => ({ ok: false })),

  delete: (keyword: string) =>
    fetchRaw(`${BASE}/${encodeURIComponent(keyword)}`, { method: 'DELETE' })
      .then(r => r.json()).catch(() => ({ ok: false })),

  toggle: (keyword: string, enabled: number) =>
    fetchRaw(`${BASE}/${encodeURIComponent(keyword)}/toggle`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }).then(r => r.json()).catch(() => ({ ok: false })),
}
