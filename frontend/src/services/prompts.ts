// Prompt Management API Service

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
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const promptsService = {
  list(params?: { category?: string; risk_level?: string; q?: string }): Promise<PromptListItem[]> {
    const qs = new URLSearchParams()
    if (params?.category) qs.set('category', params.category)
    if (params?.risk_level) qs.set('risk_level', params.risk_level)
    if (params?.q) qs.set('q', params.q)
    const query = qs.toString()
    return api(`${BASE}${query ? `?${query}` : ''}`)
  },

  registry(): Promise<PromptMeta[]> {
    return api(`${BASE}/meta/registry`)
  },

  get(key: string): Promise<PromptDetail> {
    return api(`${BASE}/${encodeURIComponent(key)}`)
  },

  versions(key: string): Promise<PromptVersion[]> {
    return api(`${BASE}/${encodeURIComponent(key)}/versions`)
  },

  getVersion(key: string, version: number): Promise<PromptVersion> {
    return api(`${BASE}/${encodeURIComponent(key)}/versions/${version}`)
  },

  diff(key: string, from: number, to: number): Promise<DiffResult> {
    return api(`${BASE}/${encodeURIComponent(key)}/diff?from=${from}&to=${to}`)
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
    return api(`${BASE}/${encodeURIComponent(key)}/audit`)
  },

  seed(): Promise<{ seeded: number }> {
    return api(`${BASE}/seed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_seed: true }),
    })
  },
}
