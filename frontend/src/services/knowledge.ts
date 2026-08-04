// Knowledge service — 对接 RAG API
// eslint-disable
export interface KnowledgeStats { doc_count: number; kb_count: number; total_chunks: number; chunk_count: number; embedding_model: string; vector_db: string; [key: string]: any }
export interface KnowledgeDoc { doc_id: string; file_name: string; doc_type: string; status: string; chunk_count: number; updated_at: string; id: string; name: string; type: string; path: string; kb_id: string; department: string; business_domain: string; size: number; chunks: number; last_indexed: string; created_at: string; embedding_model: string; chunk_size: number; overlap: number; index_version: string; hash: string; last_operation_at: string; last_operation: string; last_trace_id: string; [key: string]: any }
export interface OperationLog { id: number; doc_id: string; doc_name: string; operation: string; result: string; created_at: string; trace_id?: string; batch_id: string; detail: any; duration_ms: number; user_id: string; source: string; [key: string]: any }
export type OperationType = 'upload' | 'reindex' | 'delete' | ''

const api = (url: string, opts?: RequestInit) => fetch(url, opts).then(r => r.json()).catch(() => ({}))
const BASE = '/api/rag'

export const knowledgeService: any = {
  getStats: () => api(`${BASE}/stats`),
  listDocs: (params: any = {}) => api(`${BASE}/documents?${new URLSearchParams(params)}`),
  getDoc: (id: string) => api(`${BASE}/documents/${id}`),
  getDocuments: (params: any = {}) => api(`${BASE}/documents?${new URLSearchParams(params)}`),
  getDocument: (id: string) => api(`${BASE}/documents/${id}`),
  listOperations: (params: any = {}) => api(`${BASE}/operations?${new URLSearchParams(params)}`),
  getOperations: (params: any = {}) => api(`${BASE}/operations?${new URLSearchParams(params)}`),
  reindex: (id: string) => fetch(`${BASE}/documents/${id}/reindex`, { method: 'POST' }),
  reindexDocument: (id: string) => fetch(`${BASE}/documents/${id}/reindex`, { method: 'POST' }),
  deleteDoc: (id: string) => fetch(`${BASE}/documents/${id}`, { method: 'DELETE' }),
  deleteDocument: (id: string) => fetch(`${BASE}/documents/${id}`, { method: 'DELETE' }),
  batchDelete: (ids: string[]) => Promise.all(ids.map((id: string) => fetch(`${BASE}/documents/${id}`, { method: 'DELETE' }))),
  batchReindex: (ids: string[]) => Promise.all(ids.map((id: string) => fetch(`${BASE}/documents/${id}/reindex`, { method: 'POST' }))),
  getOperationTrace: (traceId: string) => api(`/api/observability/traces/${traceId}`),
  getChunkDetail: (chunkId: string) => api(`${BASE}/chunks/${chunkId}/detail`),
  uploadDocument: (file: File, onProgress?: any) => {
    const fd = new FormData(); fd.append('file', file)
    return fetch(`${BASE}/upload`, { method: 'POST', body: fd }).then(r => r.json())
  },
  uploadFile: (file: File, onProgress?: any) => {
    const fd = new FormData(); fd.append('file', file)
    return fetch(`${BASE}/upload`, { method: 'POST', body: fd }).then(r => r.json())
  },
}
