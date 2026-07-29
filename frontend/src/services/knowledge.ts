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
  // 列表页扩展字段
  doc_type?: string
  business_domain?: string
  last_operation?: string
  last_operation_at?: string
  last_trace_id?: string
  metadata_fingerprint?: string
  doc_version?: number
  department?: string
  kb_version?: string
}

export interface DocumentListResult {
  documents: KnowledgeDoc[]
  total: number
  page: number
  page_size: number
  current_fingerprint?: string
}

/** 文档操作类型 */
export type OperationType = 'upload' | 'reindex' | 'delete'

/** 文档操作审计日志条目 */
export interface OperationLog {
  id: number
  doc_id: string
  doc_name: string
  operation: OperationType
  user_id: string
  source: string               // "IP | User-Agent"
  trace_id: string | null      // upload/reindex 关联，trace 持久化后重启不丢
  batch_id: string | null
  result: 'success' | 'failed'
  detail: string | Record<string, unknown> | null
  duration_ms: number          // 操作耗时（毫秒）
  created_at: string
}

export interface OperationListResult {
  items: OperationLog[]
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
    kb_id?: string; department?: string
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
    onProgress?: (stage: string, message: string, durationMs?: number, stageElapsed?: Record<string, number>) => void,
    batchId?: string,
    kbId?: string,
    department?: string,
  ): Promise<{ ok: boolean; doc?: KnowledgeDoc; error?: string; duplicate?: boolean; trace_id?: string; stage_elapsed?: Record<string, number> }> {
    const fd = new FormData()
    fd.append('file', file)
    if (kbId) fd.append('kb_id', kbId)
    if (department) fd.append('department', department)
    const headers: Record<string, string> = {}
    if (batchId) headers['X-Batch-Id'] = batchId
    const res = await fetch(`${BASE}/upload`, { method: 'POST', body: fd, headers })

    // 检查响应是否 OK
    if (!res.ok) {
      // 503: 管道初始化中 → 友好提示
      if (res.status === 503) {
        let retryAfter = 15
        try {
          const errDetail = JSON.parse(await res.text())
          if (errDetail?.detail?.retry_after) retryAfter = errDetail.detail.retry_after
        } catch {}
        throw new Error(`知识库服务正在初始化，预计 ${retryAfter}s 后可用，请稍后重试`)
      }
      // 通用错误
      const text = await res.text().catch(() => '')
      throw new Error(`上传失败 (${res.status}): ${text.slice(0, 200)}`)
    }

    const data = await res.json()
    if (!data.ok || !data.upload_id) {
      return { ok: false, error: data.error || '上传失败' }
    }

    // 订阅 SSE 获取真实进度
    // P1.5 fix: 用单一 onmessage listener + 解析 data.stage 字段
    // 避免 addEventListener 注册时机的 race condition（事件可能在注册前到达丢失）
    return new Promise<{ ok: boolean; doc?: KnowledgeDoc; error?: string; duplicate?: boolean; trace_id?: string; stage_elapsed?: Record<string, number> }>((resolve) => {
      const eventSource = new EventSource(`${BASE}/upload/${data.upload_id}/stream`)
      let resolved = false

      const cleanup = () => {
        if (!resolved) {
          resolved = true
          eventSource.close()
        }
      }

      // 单一 onmessage：所有 SSE event 都触发，解析 data.stage 字段
      eventSource.onmessage = (e: MessageEvent) => {
        let payload: any = {}
        try {
          payload = JSON.parse(e.data)
        } catch {
          payload = { message: e.data }
        }
        const stage = payload.stage || 'unknown'
        // duration_ms 来自后端 span；stage_elapsed 仅终态（done/duplicate）携带
        const durationMs = typeof payload.duration_ms === 'number' ? payload.duration_ms : undefined
        const stageElapsed = payload.stage_elapsed && typeof payload.stage_elapsed === 'object'
          ? payload.stage_elapsed as Record<string, number>
          : undefined
        onProgress?.(stage, payload.message || '', durationMs, stageElapsed)

        if (stage === 'done') {
          cleanup()
          resolve({ ok: true, doc: payload.doc, trace_id: payload.trace_id || '', stage_elapsed: stageElapsed })
        } else if (stage === 'duplicate') {
          cleanup()
          resolve({ ok: true, doc: payload.doc, duplicate: true, trace_id: payload.trace_id || '', stage_elapsed: stageElapsed })
        } else if (stage === 'error') {
          cleanup()
          resolve({ ok: false, error: payload.message || '索引失败' })
        }
      }

      // SSE 通用 error 事件
      eventSource.addEventListener('error', () => {
        if (resolved) return
        cleanup()
        resolve({ ok: false, error: '进度连接中断' })
      })
    })
  },

  async deleteDocument(docId: string): Promise<{ ok: boolean; error?: string }> {
    const res = await fetch(`${BASE}/documents/${docId}`, { method: 'DELETE' })
    return res.json()
  },

  async reindexDocument(docId: string, force?: boolean): Promise<{ ok: boolean; doc_id?: string; chunk_count?: number; hash?: string; doc?: KnowledgeDoc; error?: string }> {
    const qs = force ? '?force=true' : ''
    const res = await fetch(`${BASE}/documents/${docId}/reindex${qs}`, { method: 'POST' })
    return res.json()
  },

  /**
   * 批量删除（并发）— Promise.all 并行调 DELETE，聚合结果。
   * SQLite WAL + ChromaDB 均支持并发写入，并发比串行快 N 倍。
   */
  async batchDelete(docs: { id: string; name: string }[]): Promise<{ ok: number; failed: { id: string; name: string; error: string }[]; warnings: { id: string; name: string; warnings: string[] }[] }> {
    const batchId = docs.length > 1 ? crypto.randomUUID() : undefined
    const headers: Record<string, string> = {}
    if (batchId) headers['X-Batch-Id'] = batchId

    const results = await Promise.all(
      docs.map(async ({ id, name }) => {
        try {
          const res = await fetch(`${BASE}/documents/${id}`, { method: 'DELETE', headers })
          const r = await res.json()
          return { id, name, ok: r.ok as boolean, error: r.error as string | undefined, warnings: r.warnings as string[] | undefined }
        } catch (e) {
          return { id, name, ok: false, error: (e as Error).message || '网络错误', warnings: undefined }
        }
      })
    )

    let ok = 0
    const failed: { id: string; name: string; error: string }[] = []
    const warnings: { id: string; name: string; warnings: string[] }[] = []
    for (const r of results) {
      if (r.ok) {
        ok++
        if (r.warnings?.length) warnings.push({ id: r.id, name: r.name, warnings: r.warnings })
      } else {
        failed.push({ id: r.id, name: r.name, error: r.error || '删除失败' })
      }
    }
    return { ok, failed, warnings }
  },

  /**
   * 批量重索引（串行）— 逐个调 reindex，聚合结果。
   * 不并发：后端走本地 embedding 模型，并发会 OOM/变慢。
   */
  async batchReindex(docIds: string[]): Promise<{ ok: number; failed: { id: string; error: string }[] }> {
    const batchId = docIds.length > 1 ? crypto.randomUUID() : undefined
    const failed: { id: string; error: string }[] = []
    let ok = 0
    const headers: Record<string, string> = {}
    if (batchId) headers['X-Batch-Id'] = batchId
    for (const id of docIds) {
      try {
        const qs = ''
        const res = await fetch(`${BASE}/documents/${id}/reindex${qs}`, { method: 'POST', headers })
        const r = await res.json()
        if (r.ok) ok++
        else failed.push({ id, error: r.error || '重索引失败' })
      } catch (e) {
        failed.push({ id, error: (e as Error).message })
      }
    }
    return { ok, failed }
  },

  /**
   * 批量上传（串行）— 逐个文件走 uploadDocument 两阶段 SSE，每文件独立进度回调。
   * 不并发：embedding 模型串行复用，并发压垮本地推理。
   */
  async uploadDocuments(
    files: File[],
    onPerFileProgress?: (file: File, stage: string, message: string) => void,
    batchId?: string,
  ): Promise<{ ok: number; failed: { name: string; error: string }[] }> {
    const id = batchId || crypto.randomUUID()
    const failed: { name: string; error: string }[] = []
    let ok = 0
    for (const file of files) {
      try {
        const r = await this.uploadDocument(
          file,
          (stage, message) => onPerFileProgress?.(file, stage, message),
          id,
        )
        if (r.ok) ok++
        else failed.push({ name: file.name, error: r.error || '上传失败' })
      } catch (e) {
        failed.push({ name: file.name, error: (e as Error).message })
      }
    }
    return { ok, failed }
  },

  async getChunks(docId: string): Promise<{ doc_id: string; chunks: Array<{ id: string; content: string; metadata: Record<string, unknown>; token_count: number }>; total: number }> {
    const res = await fetch(`${BASE}/documents/${docId}/chunks`)
    return res.json()
  },

  /** 获取文档完整 Chunk 文本（从 SQLite chunk_store，供 Trace 详情页查看） */
  async getChunkDetail(docId: string): Promise<{ doc_id: string; chunks: Array<{ chunk_index: number; content: string; char_count: number; keywords: string; llm_keywords?: string; llm_model?: string; section_title?: string; doc_type?: string; kb_id?: string; department?: string }>; total: number }> {
    const res = await fetch(`${BASE}/chunks/${docId}/detail`)
    return res.json()
  },

  async getOperations(params?: {
    page?: number; page_size?: number; operation?: string; doc_id?: string
  }): Promise<OperationListResult> {
    const qs = new URLSearchParams()
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== '') qs.set(k, String(v))
      })
    }
    const res = await fetch(`${BASE}/operations?${qs}`)
    return res.json()
  },
}
