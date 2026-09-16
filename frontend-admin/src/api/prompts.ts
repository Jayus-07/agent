// Prompt Management API Service

export type PromptStatus = 'draft' | 'testing' | 'evaluation' | 'passed' | 'published' | 'archived'

export const PROMPT_STATUSES: PromptStatus[] = ['draft', 'testing', 'evaluation', 'passed', 'published', 'archived']

export const STATUS_META: Record<PromptStatus, { label: string; color: string; bg: string }> = {
  draft:      { label: '草稿',   color: 'text-gray-600',  bg: 'bg-gray-100' },
  testing:    { label: '测试中', color: 'text-blue-600',   bg: 'bg-blue-100' },
  evaluation: { label: '评估中', color: 'text-amber-600',  bg: 'bg-amber-100' },
  passed:     { label: '已通过', color: 'text-green-600',  bg: 'bg-green-100' },
  published:  { label: '已发布', color: 'text-emerald-700', bg: 'bg-emerald-100' },
  archived:   { label: '已归档', color: 'text-slate-500',  bg: 'bg-slate-100' },
}

export interface PromptMeta {
  key: string
  name: string
  category: string
  risk_level: string
  variables: { name: string; required: boolean; description?: string }[]
  code_controlled: boolean
  required_substrings?: string[]
}

export interface PromptDetail extends PromptMeta {
  description: string
  active_version: number | null
  is_code_controlled: boolean
  template: string
  template_engine: string
  created_at: string
  updated_at: string
}

export interface PromptListItem {
  key: string
  name: string
  category: string
  risk_level: string
  active_version: number | null
  is_code_controlled: boolean
  variable_count: number
  latest_version_number: number | null
  latest_version_status: PromptStatus | null
  latest_version_updated_at: string | null
}

export interface PromptVersion {
  version: number
  status: string
  change_note: string
  created_by: string
  created_at: string
  variables: { name: string; required: boolean }[]
  template: string
}

export interface AuditEntry {
  id: number
  prompt_key: string
  action: string
  from_version: number | null
  to_version: number | null
  actor: string
  role: string
  detail: Record<string, unknown> | null
  created_at: string
}

export interface DiffResult {
  from_version: number
  to_version: number
  diff: string
}

export interface RenderResult {
  text: string
  key: string
  version: number | null
  source: string
}

export interface PlaygroundResult {
  rendered: RenderResult
  llm_output: string
  latency_ms: number
}

const BASE = '/api/prompts'

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    // 凭据收口（2026-09-16 方案 B）：X-API-Key 由 BFF 代理路由服务端注入；
    // 勿引用 NEXT_PUBLIC_API_KEY（NEXT_PUBLIC_* 会被内联进浏览器 bundle）。
    ...(init?.headers as Record<string, string> || {}),
  }
  const res = await fetch(url, { ...init, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const promptsService = {
  list(params?: { category?: string; risk_level?: string; q?: string; keys?: string[] }): Promise<PromptListItem[]> {
    const qs = new URLSearchParams()
    if (params?.category) qs.set('category', params.category)
    if (params?.risk_level) qs.set('risk_level', params.risk_level)
    if (params?.q) qs.set('q', params.q)
    if (params?.keys?.length) qs.set('keys', params.keys.join(','))
    const query = qs.toString()
    return api<{ items: PromptListItem[] }>(`${BASE}${query ? `?${query}` : ''}`).then(r => r.items)
  },

  registry(): Promise<PromptMeta[]> {
    return api(`${BASE}/meta/registry`).then(r => (r as any).specs ?? r)
  },

  get(key: string): Promise<PromptDetail> {
    return api(`${BASE}/${encodeURIComponent(key)}`)
  },

  versions(key: string): Promise<PromptVersion[]> {
    return api(`${BASE}/${encodeURIComponent(key)}/versions`).then(r => (r as any).items ?? r)
  },

  getVersion(key: string, version: number): Promise<PromptVersion> {
    return api(`${BASE}/${encodeURIComponent(key)}/versions/${version}`)
  },

  diff(key: string, from: number, to: number): Promise<DiffResult> {
    return api(`${BASE}/${encodeURIComponent(key)}/diff?from_version=${from}&to_version=${to}`)
  },

  createDraft(key: string, template: string, changeNote = ''): Promise<{ version: number }> {
    return api(`${BASE}/${encodeURIComponent(key)}/drafts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template, change_note: changeNote }),
    })
  },

  publish(key: string, version: number): Promise<{ active_version: number }> {
    return api(`${BASE}/${encodeURIComponent(key)}/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version }),
    })
  },

  rollback(key: string, version: number): Promise<{ active_version: number }> {
    return api(`${BASE}/${encodeURIComponent(key)}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version }),
    })
  },

  render(key: string, variables: Record<string, string>, template?: string): Promise<RenderResult> {
    return api(`${BASE}/${encodeURIComponent(key)}/render`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variables, template }),
    })
  },

  playground(key: string, variables: Record<string, string>, template?: string, model?: string): Promise<PlaygroundResult> {
    return api(`${BASE}/${encodeURIComponent(key)}/playground`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variables, template, model }),
    })
  },

  audit(key: string): Promise<AuditEntry[]> {
    return api(`${BASE}/${encodeURIComponent(key)}/audit`).then(r => (r as any).items ?? r)
  },

  seed(): Promise<{ seeded: number }> {
    return api(`${BASE}/seed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_seed: true }),
    })
  },

  transition(key: string, version: number, status: PromptStatus): Promise<{ version: number; status: PromptStatus }> {
    return api(`${BASE}/${encodeURIComponent(key)}/versions/${version}/transition`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    })
  },
}
