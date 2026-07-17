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

  /**
   * 上传文档（两阶段）：
   * 1. POST /upload → 返回 { ok, upload_id, filename }
   * 2. GET /upload/{upload_id}/stream (SSE) → 真实索引进度
   *
   * @param file 待上传文件
   * @param onProgress 阶段回调：uploading | parsing | chunking | embedding | writing | done | error
   * @returns Promise<{ ok, doc? }>
   */
  async uploadDocument(
    file: File,
    onProgress?: (stage: string, message: string) => void,
  ): Promise<{ ok: boolean; doc?: KnowledgeDoc; error?: string }> {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${BASE}/upload`, { method: 'POST', body: fd })

    // 检查响应是否 OK，避免 HTML 错误页面被当 JSON 解析
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`上传失败 (${res.status}): ${text.slice(0, 200)}`)
    }

    const data = await res.json()
    if (!data.ok || !data.upload_id) {
      return { ok: false, error: data.error || '上传失败' }
    }

    // 订阅 SSE 获取真实进度
    return new Promise((resolve) => {
      const eventSource = new EventSource(`${BASE}/upload/${data.upload_id}/stream`)
      let resolved = false

      const cleanup = () => {
        if (!resolved) {
          resolved = true
          eventSource.close()
        }
      }

      // 各阶段事件：stage 名作为 event 名
      const stages = ['uploading', 'parsing', 'chunking', 'embedding', 'writing', 'done', 'error']
      stages.forEach((stage) => {
        eventSource.addEventListener(stage, (e: MessageEvent) => {
          let payload: any = {}
          try {
            payload = JSON.parse(e.data)
          } catch {
            payload = { message: e.data }
          }
          onProgress?.(stage, payload.message || '')

          if (stage === 'done') {
            cleanup()
            resolve({ ok: true, doc: payload.doc })
          } else if (stage === 'error') {
            cleanup()
            resolve({ ok: false, error: payload.message || '索引失败' })
          }
        })
      })

      // SSE 通用 error 事件
      eventSource.addEventListener('error', (e) => {
        // readyState 0 = 连接中，1 = 已连接，2 = 已关闭
        if ((e as any).readyState === 2 && !resolved) {
          // 服务端关闭 → done/error 已发过，这里跳过
          return
        }
        cleanup()
        resolve({ ok: false, error: '进度连接中断' })
      })
    })
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
