/**
 * 智能选品漏斗 service（多域隔离收官 M4）
 *
 * 后端为 backend/app/api/routes/selection_funnel.py（前缀 /selection-funnel，
 * 经 BFF 路由 src/app/api/[...path]/route.ts 代理）。必须使用相对路径 +
 * request()，禁止 NEXT_PUBLIC_API_URL（会绕过代理）。
 * 注意与 api/selection.ts（旧 /selection 智能选品评分服务）是两条线。
 */

import { createIdempotencyKey, mutationFetchRaw, request } from '@/lib/fetcher'

const BASE = '/api/selection-funnel'

// ── 类型定义 ──────────────────────────────────

export interface FunnelImportResult {
  batch_id: string
  count: number
  notes: string[]
}

/** 导入候选池条目（字段与后端 _POOL_FIELDS 对齐，缺列容错） */
export interface FunnelCandidate {
  title: string
  url: string
  platform: string
  category: string
  price: number | null
  sales: number | null
  rating: number | null
  review_count: number | null
  history_batches?: number
}

export interface FunnelTopItem {
  rank: number | null
  title: string
  url: string
  platform: string
  price: number | null
  rating: number | null
  review_count: number | null
  highlights: string
  score_total: number | null
  margin: number | null
  gross_margin: number | null
}

export interface FunnelStageLog {
  stage: string
  kept: number
  dropped: number
  elapsed_ms?: number
}

export interface FunnelRunResult {
  conversation_id: string
  final_answer: string
  funnel_context: {
    status?: string
    top?: FunnelTopItem[]
    stage_summary?: FunnelStageLog[]
    config_snapshot?: Record<string, unknown>
    [key: string]: unknown
  }
}

export type FunnelKind = 'products' | 'keywords' | 'reviews'

// ── API ──────────────────────────────────────

/** 上传表格导入（CSV/TSV/XLSX，首行表头） */
export async function importFunnelFile(
  file: File,
  opts: { kind: FunnelKind; category?: string; platform?: string },
): Promise<FunnelImportResult> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('kind', opts.kind)
  if (opts.category) fd.append('category', opts.category)
  if (opts.platform) fd.append('platform', opts.platform)
  const res = await mutationFetchRaw(`${BASE}/import/file`, {
    operation: 'selection_funnel.import',
    idempotencyKey: createIdempotencyKey(),
    dedupeKey: [
      opts.kind, opts.category || '', opts.platform || '',
      file.name, file.size, file.lastModified,
    ].join(':'),
    method: 'POST',
    body: fd,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok || !data) {
    throw new Error(data?.detail || `导入失败（HTTP ${res.status}）`)
  }
  return data as FunnelImportResult
}

/** 粘贴表格文本导入（Excel Ctrl+C 复制的 TSV） */
export function importFunnelText(
  content: string,
  opts: { kind: FunnelKind; category?: string; platform?: string },
): Promise<FunnelImportResult> {
  return request<FunnelImportResult>(`${BASE}/import/text`, {
    method: 'POST',
    body: {
      content,
      kind: opts.kind,
      category: opts.category || '',
      platform: opts.platform || '',
    },
  })
}

/** 导入候选池（粗过滤视图） */
export function listFunnelCandidates(
  category = '',
  platform = '',
): Promise<{ count: number; items: FunnelCandidate[] }> {
  const qs = new URLSearchParams()
  if (category) qs.set('category', category)
  if (platform) qs.set('platform', platform)
  const q = qs.toString()
  return request(`${BASE}/import/candidates${q ? `?${q}` : ''}`)
}

/** 执行漏斗（专属页直达，不经主图；同 conversation_id 跨轮续跑） */
export function runFunnel(req: {
  message?: string
  category?: string
  platform?: string
  conversation_id?: string
}): Promise<FunnelRunResult> {
  return request<FunnelRunResult>(`${BASE}/run`, {
    method: 'POST',
    body: {
      message: req.message || '',
      category: req.category || '',
      platform: req.platform || '',
      conversation_id: req.conversation_id || '',
    },
    timeout: 180000,
  })
}

/** 清除指定导入批次 */
export function clearFunnelBatch(batchId: string): Promise<{ removed: number }> {
  return request(`${BASE}/import/batch/${encodeURIComponent(batchId)}`, {
    method: 'DELETE',
  })
}
