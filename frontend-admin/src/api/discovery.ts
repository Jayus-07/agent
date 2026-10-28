/**
 * Tool Discovery 策略与模拟器 API（决策 #18，B1 前端）
 *
 * 后端：backend/app/api/routes/admin_tools.py（/admin/tools 前缀）。
 * B1 边界：策略表 + 只读候选解析（模拟器），不进运行时路由链路。
 */
import { mutationRequest, request } from '@/api/client'

export type DiscoveryMode = 'adaptive' | 'reasoning' | 'fast'

export interface DiscoveryPolicy {
  id: string
  name: string
  active: boolean
  mode: DiscoveryMode
  top_k: number
  max_rounds: number
  cost_budget_milliunits: number
  timeout_ms: number
  prompt_key: string
  tenant_id: string
  created_by: string
  updated_by: string
  created_at: string
  updated_at: string
}

export interface DiscoveryPolicyView {
  /** 生效策略（无 active 行时为代码内默认值，active=false 标记） */
  policy: DiscoveryPolicy
  policies: DiscoveryPolicy[]
}

export interface DiscoveryPolicyUpsertInput {
  name: string
  mode: DiscoveryMode
  top_k: number
  max_rounds: number
  cost_budget_milliunits: number
  timeout_ms: number
  prompt_key: string
}

export async function getDiscoveryPolicy(): Promise<DiscoveryPolicyView> {
  return request<DiscoveryPolicyView>('/api/admin/tools/discovery/policy')
}

export async function updateDiscoveryPolicy(
  input: DiscoveryPolicyUpsertInput,
): Promise<DiscoveryPolicy> {
  return mutationRequest<DiscoveryPolicy>('/api/admin/tools/discovery/policy', {
    operation: 'tool_discovery_policy_upsert',
    method: 'PUT',
    body: input,
  })
}

export interface DiscoveryCandidate {
  name: string
  risk: string
  source: string
  permission_note: string
}

export interface DiscoverySimulateResult {
  query: string
  domain: string
  product_id: string
  candidates: DiscoveryCandidate[]
  policy: DiscoveryPolicy
  excluded_count: number
  candidate_count: number
  source: string
  notes: string[]
}

export async function simulateDiscovery(input: {
  query: string
  domain?: string
  top_k?: number
  product_id?: string
}): Promise<DiscoverySimulateResult> {
  return mutationRequest<DiscoverySimulateResult>(
    '/api/admin/tools/discovery/simulate',
    { operation: 'tool_discovery_simulate', body: input },
  )
}
