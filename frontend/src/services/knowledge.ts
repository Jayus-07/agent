// Service — RAG Knowledge Center API

const BASE = '/api/rag'

export interface KnowledgeStats {
  kb_count: number; doc_count: number; chunk_count: number
  embedding_model: string; vector_db: string; vector_db_path?: string
}

export interface KnowledgeDoc {
  id: string; name: string; path?: string; kb_id: string; type: string
  size: number; chunk_count: number; chunks?: number  // chunks 兼容旧字段
  hash: string; status: string
  embedding_model?: string; index_version?: number
  last_indexed?: string; created_at?: string; updated_at?: string
  parse_time_ms?: number; index_time_ms?: number
  // 详情页扩展字段
  chunk_size?: number; overlap?: number
}

export interface DocumentListResult {
  documents: KnowledgeDoc[]
  total: number
  page: number
  page_size: number
}

export const knowledgeService = {
  async getStats(): Promise<KnowledgeStats> {
    const res = await fetch(`${BASE}/stats`)
    return res.json()
  },

  async getDocuments(params?: {
    keyword?: string; type?: string; status?: string
    page?: number; page_size?: number
  }): Promise<DocumentListResult> {
    const qs = new URLSearchParams()
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== '') qs.set(k, String(v))
      })
    }
    const res = await fetch(`${BASE}/documents?${qs}`)
    return res.json()
  },

  async getDocument(docId: string): Promise<{ ok: boolean; doc?: KnowledgeDoc; error?: string }> {
    const res = await fetch(`${BASE}/documents/${docId}`)
    return res.json()
  },

  async uploadDocument(file: File): Promise<{ ok: boolean; doc_id?: string; error?: string }> {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${BASE}/upload`, { method: 'POST', body: fd })
    return res.json()
  },

  async deleteDocument(docId: string): Promise<{ ok: boolean }> {
    const res = await fetch(`${BASE}/documents/${docId}`, { method: 'DELETE' })
    return res.json()
  },

  async reindexDocument(docId: string, force?: boolean): Promise<{ ok: boolean; doc_id?: string; chunk_count?: number; hash?: string; doc?: KnowledgeDoc; error?: string }> {
    const qs = force ? '?force=true' : ''
    const res = await fetch(`${BASE}/documents/${docId}/reindex${qs}`, { method: 'POST' })
    return res.json()
  },

  async getChunks(docId: string): Promise<{ doc_id: string; chunks: Array<{ id: string; content: string; metadata: Record<string, unknown>; token_count: number }>; total: number }> {
    const res = await fetch(`${BASE}/documents/${docId}/chunks`)
    return res.json()
  },
}
