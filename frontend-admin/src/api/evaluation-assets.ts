import { request } from '@/api/client'

const BASE = '/api/evaluation'
export interface ImportManifest { schema_version: string; dataset_key: string; name: string; version: string; module: string; description?: string; csv_mapping?: Record<string, string>; case_schema?: Record<string, unknown> }
export interface ImportedDataset {
  import_id: string; manifest: ImportManifest; content_hash: string; owner: string; created_at: string;
  case_count: number; active_case_count?: number; review: { decision: string; reason?: string; reviewer?: string }; preview: Array<{ id: string; question: string; expected: Record<string, unknown> }>
}
export interface EvaluationBinding {
  binding_id: string; import_id: string; kb_id: string; dataset_name: string; dataset_version: string; content_hash: string
  latest_run?: { run_id: string; validity: string; total: number; gate?: string } | null
  mode?: 'retrieval' | 'answer'; split?: string; snapshot_id?: string
}
export interface EvaluationTask { task_id: string; run_id: string; status: string; progress?: string; error?: string; output?: { run_id: string; validity: string; total: number } }
export interface ImportValidation { valid: boolean; manifest?: ImportManifest; case_count?: number; errors: Array<{ line: number; field: string; message: string }>; preview?: ImportedDataset['preview'] }

function uploadBody(file: File, manifest: ImportManifest) {
  const body = new FormData()
  body.append('file', file)
  body.append('manifest', JSON.stringify(manifest))
  return body
}
export const evaluationAssets = {
  list: () => request<{ items: ImportedDataset[] }>(`${BASE}/dataset-imports`).then(r => r.items),
  detail: (id: string) => request<ImportedDataset & { cases: unknown[] }>(`${BASE}/dataset-imports/${id}`),
  validate: (file: File, manifest: ImportManifest) => request<ImportValidation>(`${BASE}/dataset-imports/validate`, { method: 'POST', body: uploadBody(file, manifest) }),
  import: (file: File, manifest: ImportManifest) => request<ImportedDataset>(`${BASE}/dataset-imports`, { method: 'POST', body: uploadBody(file, manifest) }),
  review: (id: string, decision: string, reason: string) => request(`${BASE}/dataset-imports/${id}/review`, { method: 'POST', body: { decision, reason } }),
  bindings: (kbId?: string) => request<{ items: EvaluationBinding[] }>(`${BASE}/evaluation-bindings${kbId ? `?kb_id=${encodeURIComponent(kbId)}` : ''}`).then(r => r.items),
  bind: (importId: string, kbId: string, options: { mode: string; split: string; snapshot_id?: string; subject_user_id?: number | null; min_samples?: number; pass_rate?: number } = { mode: 'retrieval', split: '' }) => request<EvaluationBinding>(`${BASE}/evaluation-bindings`, { method: 'POST', body: { import_id: importId, kb_id: kbId, ...options } }),
  freeze: (kbId: string) => request<{ snapshot_id: string; warnings?: unknown[] }>(`${BASE}/evaluation-corpus/${encodeURIComponent(kbId)}/snapshots`, { method: 'POST', timeout: 120000 }),
  run: (bindingId: string, requestId: string) => request<EvaluationTask>(`${BASE}/evaluation-bindings/${bindingId}/run`, { method: 'POST', body: { request_id: requestId } }),
  task: (id: string) => request<EvaluationTask>(`${BASE}/evaluation-tasks/${id}`),
  cancel: (id: string) => request(`${BASE}/evaluation-tasks/${id}/cancel`, { method: 'POST' }),
  resume: (id: string) => request<EvaluationTask>(`${BASE}/evaluation-tasks/${id}/resume`, { method: 'POST' }),
  revise: (id: string, version: string, changes: Record<string, unknown>) => request<ImportedDataset>(`${BASE}/dataset-imports/${id}/revisions`, { method: 'POST', body: { version, changes } }),
  diff: (id: string, other: string) => request(`${BASE}/dataset-imports/${id}/diff/${other}`),
  label: (id: string, label: string) => request(`${BASE}/dataset-imports/${id}/labels`, { method: 'POST', body: { label } }),
  promoteCandidate: (candidate_id: string, manifest: Record<string, unknown>, expected: Record<string, unknown>) => request(`${BASE}/dataset-candidate-promotions`, { method: 'POST', body: { candidate_id, manifest, expected } }),
  publicationPolicy: (kbId: string) => request<{ policy: { binding_id: string; enabled: boolean } | null }>(`${BASE}/evaluation-publication-policies/${encodeURIComponent(kbId)}`),
  setPublicationPolicy: (kbId: string, bindingId: string, enabled: boolean) => request(`${BASE}/evaluation-publication-policies/${encodeURIComponent(kbId)}`, { method: 'POST', body: { binding_id: bindingId, enabled } }),
  synthesize: (snapshot_id: string, manifest: ImportManifest, count: number, request_id: string) => request<EvaluationTask>(`${BASE}/dataset-synthesis`, { method: 'POST', body: { snapshot_id, manifest, count, request_id } }),
}

export function downloadEvalTemplate() {
  const template = {
    manifest: { schema_version: 'rag-eval/1.0', dataset_key: 'policy_golden', name: '制度评测集', version: '1.0.0', module: 'rag' },
    cases: [{ id: 'POLICY-001', question: '请替换成真实业务问题', tier: 'core', query_type: 'single_doc', expected: {
      behavior: 'answer', evidence: [{ doc_id: '替换为文档详情中的ID', quote: '替换为原文中完整的证据句子' }], reference_answer: '填写经过核验的参考答案', required_facts: [],
    } }],
  }
  const url = URL.createObjectURL(new Blob([JSON.stringify(template, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url; link.download = 'rag-eval-template.json'; link.click()
  URL.revokeObjectURL(url)
}
