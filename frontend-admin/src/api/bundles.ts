/**
 * 能力包（CapabilityBundle）管理 API（Batch 2）。
 *
 * 能力包 = 静态 capability 与动态 mcp.* 的授权聚合单元；
 * 授权/撤销立即生效（动态成员同步 ai.mcp_tool_grants）。
 */
import { mutationRequest, request } from './client'

export interface CapabilityBundle {
  id: string
  name: string
  description: string
  domain: string
  enabled: boolean
  version: number
  capabilities: string[]
  granted_products?: string[]
  grants?: {
    product_key: string
    active: boolean
    granted_by: string
    granted_at: string
    revoked_at: string | null
  }[]
  updated_at?: string | null
}

export async function getCapabilityBundles(): Promise<CapabilityBundle[]> {
  const data = await request<{ count: number; bundles: CapabilityBundle[] }>(
    '/api/admin/capability-bundles')
  return data.bundles
}

export async function createCapabilityBundle(input: {
  name: string
  description: string
  domain: string
  capabilities: string[]
}): Promise<{ id: string }> {
  return mutationRequest('/api/admin/capability-bundles', {
    operation: 'capability_bundle_create',
    method: 'POST',
    body: input,
  })
}

export async function updateCapabilityBundle(
  id: string,
  input: { name?: string; description?: string; domain?: string;
           enabled?: boolean; capabilities?: string[] },
): Promise<{ id: string; updated: boolean }> {
  return mutationRequest(`/api/admin/capability-bundles/${id}`, {
    operation: 'capability_bundle_update',
    method: 'PUT',
    body: input,
  })
}

export async function grantCapabilityBundle(
  id: string,
  productKey: 'agent' | 'customer_service',
  revoke: boolean,
): Promise<{ bundle_id: string; product_key: string; revoked: boolean }> {
  return mutationRequest(`/api/admin/capability-bundles/${id}/grants`, {
    operation: 'capability_bundle_grant',
    method: 'POST',
    body: { product_key: productKey, revoke },
  })
}
