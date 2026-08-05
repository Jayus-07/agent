// Knowledge service — 对接 RAG API
// eslint-disable
export interface KnowledgeStats { doc_count: number; kb_count: number; total_chunks: number; chunk_count: number; embedding_model: string; vector_db: string; [key: string]: any }
export interface KnowledgeDoc { doc_id: string; file_name: string; doc_type: string; status: string; chunk_count: number; updated_at: string; id: string; name: string; type: string; path: string; kb_id: string; department: string; business_domain: string; size: number; chunks: number; last_indexed: string; created_at: string; embedding_model: string; chunk_size: number; overlap: number; index_version: string; hash: string; last_operation_at: string; last_operation: string; last_trace_id: string; [key: string]: any }
export interface OperationLog { id: number; doc_id: string; doc_name: string; operation: string; result: string; created_at: string; trace_id?: string; batch_id: string; detail: any; duration_ms: number; user_id: string; source: string; [key: string]: any }
/** 操作日志里的 operation 字段类型 — /knowledge/operations 页筛选下拉依赖此类型 */
export type OperationType = 'upload' | 'reindex' | 'delete' | ''

const BASE = '/api/rag'

/** 构建查询字符串，自动过滤 undefined/null/空字符串，避免 URLSearchParams 将其转为字面字符串 "undefined" */
const qs = (params: Record<string, any>) => {
  const clean: Record<string, string> = {}
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') clean[k] = String(v)
  }
  return new URLSearchParams(clean).toString()
}

export const knowledgeService: any = {
  getStats: () => fetch(`${BASE}/stats`).then(r => r.json()).catch(() => ({})),

  getDocuments: (params: any = {}) =>
    fetch(`${BASE}/documents?${qs(params)}`).then(r => r.json()).catch(() => ({ documents: [], total: 0 })),

  getDocument: (id: string) =>
    fetch(`${BASE}/documents/${id}`).then(r => r.json()).catch(() => ({})),

  /**
   * 上传文档（两阶段）：
   * 1. POST /upload → 返回 { ok, upload_id, filename }
   * 2. GET /upload/{upload_id}/stream (SSE) → 推送索引进度（9 阶段 + 耗时）
   *
   * @param file 待上传文件
   * @param onProgress 阶段回调：(stage, message, durationMs?, stageElapsed?)
   * @param batchId 多文件上传批次 ID（透传 X-Batch-Id 头给后端）
   * @param kbId 目标知识库 ID
   * @param department 目标部门
   */
  uploadDocument: (
    file: File,
    onProgress?: (stage: string, message: string, durationMs?: number, stageElapsed?: Record<string, number>) => void,
    batchId?: string,
    kbId?: string,
    department?: string,
  ): Promise<{ ok: boolean; doc?: KnowledgeDoc; error?: string; duplicate?: boolean; trace_id?: string; stage_elapsed?: Record<string, number>; total_ms?: number }> => {
    const fd = new FormData()
    fd.append('file', file)
    if (kbId) fd.append('kb_id', kbId)
    if (department) fd.append('department', department)
    const headers: Record<string, string> = {}
    if (batchId) headers['X-Batch-Id'] = batchId

    return fetch(`${BASE}/upload`, { method: 'POST', body: fd, headers })
      .then(async (res) => {
        // 503: 管道初始化中 → 友好提示
        if (res.status === 503) {
          let retryAfter = 15
          try {
            const errDetail = JSON.parse(await res.text())
            if (errDetail?.detail?.retry_after) retryAfter = errDetail.detail.retry_after
          } catch { /* ignore */ }
          throw new Error(`知识库服务正在初始化，预计 ${retryAfter}s 后可用，请稍后重试`)
        }
        if (!res.ok) {
          const text = await res.text().catch(() => '')
          throw new Error(`上传失败 (${res.status}): ${text.slice(0, 200)}`)
        }
        return res.json()
      })
      .then((data) => {
        if (!data.ok || !data.upload_id) {
          return { ok: false, error: data.error || '上传失败' }
        }

        // 订阅 SSE 获取索引进度
        return new Promise((resolve) => {
          const eventSource = new EventSource(`${BASE}/upload/${data.upload_id}/stream`)
          let resolved = false

          const cleanup = () => {
            if (!resolved) { resolved = true; eventSource.close() }
          }

          eventSource.onmessage = (e: MessageEvent) => {
            let payload: any = {}
            try { payload = JSON.parse(e.data) } catch { payload = { message: e.data } }
            const stage = payload.stage || 'unknown'
            const durationMs = typeof payload.duration_ms === 'number' ? payload.duration_ms : undefined
            const stageElapsed = payload.stage_elapsed && typeof payload.stage_elapsed === 'object'
              ? payload.stage_elapsed as Record<string, number>
              : undefined
            const totalMs = typeof payload.total_ms === 'number' ? payload.total_ms : undefined
            onProgress?.(stage, payload.message || '', durationMs, stageElapsed)

            if (stage === 'done') {
              cleanup()
              resolve({ ok: true, doc: payload.doc, trace_id: payload.trace_id || '', stage_elapsed: stageElapsed, total_ms: totalMs })
            } else if (stage === 'duplicate') {
              cleanup()
              resolve({ ok: true, doc: payload.doc, duplicate: true, trace_id: payload.trace_id || '', stage_elapsed: stageElapsed, total_ms: totalMs })
            } else if (stage === 'error') {
              cleanup()
              resolve({ ok: false, error: payload.message || '索引失败' })
            }
          }

          eventSource.addEventListener('error', () => {
            if (resolved) return
            cleanup()
            resolve({ ok: false, error: '进度连接中断' })
          })
        })
      })
  },

  deleteDocument: (id: string) =>
    fetch(`${BASE}/documents/${id}`, { method: 'DELETE' }).then(r => r.json()).catch(() => ({ ok: false })),

  reindexDocument: (id: string) =>
    fetch(`${BASE}/documents/${id}/reindex`, { method: 'POST' }).then(r => r.json()).catch(() => ({ ok: false })),

  /**
   * 批量删除（并发）— 使用 X-Batch-Id 头关联同一批次，操作中心按批次折叠展示。
   */
  batchDelete: (docs: { id: string; name: string }[]) => {
    const batchId = docs.length > 1 ? crypto.randomUUID() : undefined
    const headers: Record<string, string> = {}
    if (batchId) headers['X-Batch-Id'] = batchId

    return Promise.all(
      docs.map(async ({ id, name }) => {
        try {
          const res = await fetch(`${BASE}/documents/${id}`, { method: 'DELETE', headers })
          const r = await res.json()
          return { id, name, ok: r.ok as boolean, error: r.error as string | undefined, warnings: r.warnings as string[] | undefined }
        } catch (e) {
          return { id, name, ok: false, error: (e as Error).message || '网络错误', warnings: undefined }
        }
      })
    ).then((results) => {
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
    })
  },

  /**
   * 批量重索引（串行）— 避免并发压垮本地 embedding 模型。
   */
  batchReindex: async (ids: string[]) => {
    const batchId = ids.length > 1 ? crypto.randomUUID() : undefined
    const failed: { id: string; error: string }[] = []
    let ok = 0
    const headers: Record<string, string> = {}
    if (batchId) headers['X-Batch-Id'] = batchId
    for (const id of ids) {
      try {
        const res = await fetch(`${BASE}/documents/${id}/reindex`, { method: 'POST', headers })
        const r = await res.json()
        if (r.ok) ok++
        else failed.push({ id, error: r.error || '重索引失败' })
      } catch (e) {
        failed.push({ id, error: (e as Error).message })
      }
    }
    return { ok, failed }
  },

  getOperations: (params: any = {}) =>
    fetch(`${BASE}/operations?${qs(params)}`).then(r => r.json()).catch(() => ({ items: [], total: 0 })),

  getChunkDetail: (chunkId: string) =>
    fetch(`${BASE}/chunks/${chunkId}/detail`).then(r => r.json()).catch(() => ({})),
}
