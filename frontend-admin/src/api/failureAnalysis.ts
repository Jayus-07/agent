/**
 * AI 失败分析 API（决策 #20 前端）
 *
 * 后端：backend/app/api/routes/admin_tools.py（/admin/tools 前缀）。
 * 边界：只生成建议与候选评测用例（evalCandidate），不自动应用/改写任何
 * 正式配置；候选用例需走评测中心审核与回归后才可采纳。
 */
import { mutationRequest, request } from '@/api/client'

export interface FailureAnalysisCandidateCase {
  question: string
  expected_capability: string
  expectation_note: string
}

export interface FailureAnalysisCluster {
  tool: string
  error_class: string
  attribution: string
  suggestions: string[]
  candidate_eval_cases: FailureAnalysisCandidateCase[]
}

export interface FailureAnalysisGroup {
  tool: string
  error_class: string
  count: number
  samples: string[]
}

/** POST /admin/tools/failure-analysis 响应 */
export interface FailureAnalysisResult {
  analysis_id: string
  clusters: FailureAnalysisCluster[]
  created_at: string
}

/** GET /admin/tools/failure-analysis 列表项 */
export interface FailureAnalysisRecord {
  id: string
  window_minutes: number
  input_summary: { window_minutes?: number; groups?: FailureAnalysisGroup[] }
  result: { clusters?: FailureAnalysisCluster[] }
  model: string
  created_by: string
  created_at: string
  cluster_count: number
}

export async function runFailureAnalysis(input: {
  window_minutes: number
  limit?: number
}): Promise<FailureAnalysisResult> {
  return mutationRequest<FailureAnalysisResult>(
    '/api/admin/tools/failure-analysis',
    { operation: 'tool_failure_analysis_run', body: input },
  )
}

export async function getFailureAnalyses(limit = 10): Promise<{
  count: number
  analyses: FailureAnalysisRecord[]
}> {
  return request(`/api/admin/tools/failure-analysis?limit=${limit}`)
}
