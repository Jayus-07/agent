// API Client — 数据接入 + 处理 + 资产

const BASE = '/api/data'
const ASSETS = '/api/assets'
const PIPELINE = '/api/pipeline'

export interface UploadResult {
  ok: boolean; file_id?: string; filename?: string; size_bytes?: number
  total_rows?: number; fields?: { name: string; type: string }[]; preview?: Record<string, any>[]
  error?: string
}

export interface DatasetInfo { id: string; name: string; source: string; rows: number; fields: string[]; desc: string }

export interface PipelineJob {
  id: string; name: string; inputRows: number; outputRows: number
  errors: number; quality: number; status: string; elapsed: string
  stages: { name: string; status: string; rows: number; removed?: number }[]
}

export interface DataAsset {
  id: string; name: string; source: string; rows: number; fields: number
  field_names?: string[]; quality: number; status: string
}

export async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/upload`, { method: 'POST', body: form })
  return res.json()
}

export async function listDatasets(): Promise<DatasetInfo[]> {
  const res = await fetch(`${BASE}/datasets`)
  const data = await res.json()
  return data.datasets || []
}

export async function generateData(types: string[], count: number): Promise<any> {
  const res = await fetch(`${BASE}/generate?${types.map(t => `types=${t}`).join('&')}&count=${count}`, { method: 'POST' })
  return res.json()
}

export async function runPipeline(fileId: string, steps: string = 'detect,clean,dedup,convert'): Promise<{ ok: boolean; job?: PipelineJob; error?: string }> {
  const form = new FormData()
  form.append('file_id', fileId)
  form.append('steps', steps)
  const res = await fetch(`${BASE}/pipeline/run`, { method: 'POST', body: form })
  return res.json()
}

export async function pipelineHistory(): Promise<PipelineJob[]> {
  const res = await fetch(`${BASE}/pipeline/history`)
  const data = await res.json()
  return data.jobs || []
}

export async function listAssets(): Promise<DataAsset[]> {
  const res = await fetch(`${ASSETS}`)
  const data = await res.json()
  return data.assets || []
}

// ══════════════════════════════════════════
// Data Collection Center — 采集任务 API
// ══════════════════════════════════════════

export interface CollectResult {
  ok: boolean
  task_id?: string
  dataset: string
  status: string
  elapsed_ms: number
  parsed_rows?: number
  cleaned_rows?: number
  dedup_removed?: number
  null_filled?: Record<string, number>
  inserted?: number
  summary?: Record<string, { mean: number }>
  error?: string
}

export interface CollectAllResult {
  ok: boolean
  total: number
  success: number
  failed: number
  total_parsed: number
  total_cleaned: number
  total_elapsed_ms: number
  results: CollectResult[]
}

/** 触发单次 DCC 数据采集 */
export async function triggerCollect(dataset: string, enableWrite: boolean = false): Promise<CollectResult> {
  const form = new FormData()
  form.append('dataset', dataset)
  form.append('enable_write', String(enableWrite))
  const res = await fetch(`${BASE}/collect`, { method: 'POST', body: form })
  return res.json()
}

/** 触发全部 5 个数据集采集 */
export async function triggerCollectAll(enableWrite: boolean = false): Promise<CollectAllResult> {
  const form = new FormData()
  form.append('enable_write', String(enableWrite))
  const res = await fetch(`${BASE}/collect/all`, { method: 'POST', body: form })
  return res.json()
}

/** DCC 采集历史 */
export async function collectHistory(limit: number = 20): Promise<{ total: number; jobs: CollectResult[] }> {
  const res = await fetch(`${BASE}/collect/history?limit=${limit}`)
  return res.json()
}
