/**
 * MCP 动态工具 API（/mcp 连接中心 + /tools Tool 治理的数据源，ADR-0011）
 *
 * 后端：backend/app/api/routes/admin_mcp.py（/api/admin/mcp/*）
 * 契约：读接口裸 dict；写接口带 Idempotency-Key（mutationRequest 自动注入）。
 * 凭据安全：服务端只回 fingerprint/last4，secret 仅在创建/更新时上行一次。
 */
import { mutationRequest, request } from '@/api/client'

// ── 类型 ────────────────────────────────────────────────────

export interface McpCredentialMeta {
  configured: boolean
  fingerprint: string
  last4: string
  version: number
}

export interface McpConnection {
  id: string
  name: string
  base_url: string
  transport: string
  auth_type: 'none' | 'static_bearer'
  network_scope: 'public' | 'private'
  enabled: boolean
  status: 'never_connected' | 'connected' | 'error' | 'disabled'
  last_error: string
  last_synced_at: string | null
  discovery_version: number
  credential: McpCredentialMeta
  tenant_id: string
  created_by: string
  updated_by: string
  updated_at: string | null
  /** 健康巡检（Batch 5-3）：最近探活时间与连续失败计数（退避依据） */
  last_checked_at?: string | null
  probe_fail_count?: number
}

export interface McpHealthEvent {
  ok: boolean
  error: string
  elapsed_ms: number
  source: 'monitor' | 'manual'
  created_at: string
}

export interface McpConnectionHealth {
  id: string
  name: string
  transport: string
  status: string
  network_scope: string
  last_error: string
  last_checked_at: string | null
  probe_fail_count: number
  probe_interval_seconds: number
  next_probe_in_seconds: number
  tool_count: number
  approved_count: number
  recent_events: McpHealthEvent[]
}

export async function getMcpHealthOverview(): Promise<{
  connections: McpConnectionHealth[]
  probe_interval_seconds: number
}> {
  return request('/api/admin/mcp/health')
}

export interface McpToolGrant {
  product_key: 'agent' | 'customer_service'
  granted_by: string
  granted_at: string
  revoked_at?: string | null
  enabled?: boolean
}

export interface McpToolSnapshot {
  discovery_version: number
  content_hash: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown> | null
  description: string
  discovered_at: string
  /** HTTP API 工具：该版本的调用定义（MCP 工具为 null） */
  invocation?: HttpToolInvocation | null
}

export interface McpGovernance {
  operation?: string
  risk_level?: string
  confirmation_policy?: string
  timeout_ms?: number
  /** 运行时策略（后端 derive_tool_spec 的实际读取路径） */
  runtime?: { timeout_ms?: number; max_calls_per_request?: number }
  required_roles?: string[]
  /** 业务域（决策 #15 硬隔离）：审核登记，授权时服务端按禁行域拦截 */
  domain?: string
  planner_example?: Record<string, unknown>
  [key: string]: unknown
}

export interface McpTool {
  id: number
  connection_id: string
  connection_name?: string
  conn_transport?: 'streamable_http' | 'http_api'
  tool_name: string
  capability: string
  capability_slug: string
  description: string
  status: 'pending' | 'approved' | 'rejected' | 'disabled' | 'retired'
  content_hash: string
  approved_hash: string
  schema_changed: boolean
  governance: McpGovernance
  invocation?: HttpToolInvocation
  examples: string[]
  review_notes: string
  reviewed_by: string
  reviewed_at: string | null
  last_seen_version: number
  retired_reason: string
  updated_at: string | null
  grants?: McpToolGrant[]
  snapshots?: McpToolSnapshot[]
}

/** HTTP API 工具的受控调用定义（method/path/query/headers/body_from） */
export interface HttpToolInvocation {
  method: 'GET' | 'POST' | 'PUT'
  path: string
  query?: Record<string, string>
  headers?: Record<string, string>
  body_from?: string
}

export interface McpOverview {
  loaded: boolean
  active_tools: string[]
  connection_count: number
  grant_count: number
  derive_errors: { capability: string; reason: string }[]
  refreshed_at: number
}

export interface ConnectionTestResult {
  ok: boolean
  elapsed_ms: number
  tool_count?: number
  tool_names?: string[]
  error_class?: string
  error?: string
}

export interface DiscoverResult {
  ok: boolean
  elapsed_ms: number
  discovery_version?: number
  new?: { tool: string; capability: string }[]
  changed?: { tool: string; reason: string }[]
  unchanged?: { tool: string }[]
  skipped?: { tool: string; reason: string }[]
  retired?: { tool: string }[]
  error_class?: string
  error?: string
}

export interface TestInvokeResult {
  tool_id: number
  capability: string
  product_key: string
  tool_status: string
  error_code: string
  error_message: string
  elapsed_ms: number
  result: unknown
}

// ── 读接口 ──────────────────────────────────────────────────

export async function getMcpOverview(): Promise<McpOverview> {
  return request<McpOverview>('/api/admin/mcp/overview')
}

export async function getMcpConnections(): Promise<McpConnection[]> {
  const data = await request<{ connections: McpConnection[] }>(
    '/api/admin/mcp/connections')
  return data.connections ?? []
}

export async function getConnectionDetail(id: string): Promise<McpConnection> {
  return request<McpConnection>(`/api/admin/mcp/connections/${id}`)
}

export async function getConnectionTools(id: string): Promise<McpTool[]> {
  const data = await request<{ tools: McpTool[] }>(
    `/api/admin/mcp/connections/${id}/tools`)
  return data.tools ?? []
}

export async function getMcpTools(statusFilter = ''): Promise<McpTool[]> {
  const qs = statusFilter ? `?status_filter=${encodeURIComponent(statusFilter)}` : ''
  const data = await request<{ tools: McpTool[] }>(`/api/admin/mcp/tools${qs}`)
  return data.tools ?? []
}

export async function getMcpToolDetail(id: number): Promise<McpTool> {
  return request<McpTool>(`/api/admin/mcp/tools/${id}`)
}

/**
 * 配置导出（GET /admin/mcp/export，只读）：连接声明 + 能力包 + 授权关系。
 * 服务端红线：凭据密文/fingerprint/last4 绝不导出，URL 内嵌凭据导出前净化；
 * 前端只负责取 JSON 并触发浏览器下载。
 */
export async function exportMcpConfiguration(): Promise<unknown> {
  return request<unknown>('/api/admin/mcp/export')
}

// ── 写接口（幂等键由 mutationRequest 自动注入）────────────────

export interface ConnectionInput {
  name: string
  base_url: string
  transport?: 'streamable_http' | 'http_api'
  auth_type: 'none' | 'static_bearer'
  secret?: string
  network_scope: 'public' | 'private'
  enabled?: boolean
}

export async function createMcpConnection(input: ConnectionInput) {
  return mutationRequest<{ id: string }>('/api/admin/mcp/connections', {
    operation: 'mcp_connection_create',
    method: 'POST',
    body: input,
  })
}

export async function updateMcpConnection(
  id: string,
  input: Partial<ConnectionInput>,
) {
  return mutationRequest<{ id: string; updated: boolean }>(
    `/api/admin/mcp/connections/${id}`,
    {
      operation: 'mcp_connection_update',
      method: 'PUT',
      body: input,
    },
  )
}

export async function deleteMcpConnection(id: string) {
  return mutationRequest<{ id: string; deleted: boolean }>(
    `/api/admin/mcp/connections/${id}`,
    {
      operation: 'mcp_connection_delete',
      method: 'DELETE',
    },
  )
}

export async function testMcpConnection(id: string): Promise<ConnectionTestResult> {
  return mutationRequest<ConnectionTestResult>(
    `/api/admin/mcp/connections/${id}/test`,
    { operation: 'mcp_connection_test', method: 'POST', body: {} },
  )
}

export async function discoverMcpTools(id: string): Promise<DiscoverResult> {
  return mutationRequest<DiscoverResult>(
    `/api/admin/mcp/connections/${id}/discover`,
    { operation: 'mcp_connection_discover', method: 'POST', body: {} },  )
}

export async function setMcpConnectionEnabled(id: string, enabled: boolean) {
  const action = enabled ? 'enable' : 'disable'
  return mutationRequest<{ id: string; enabled: boolean }>(
    `/api/admin/mcp/connections/${id}/${action}`,
    { operation: `mcp_connection_${action}`, method: 'POST', body: {} },
  )
}

export interface ReviewInput {
  action: 'approve' | 'reject'
  description?: string
  governance?: McpGovernance
  examples?: string[]
  notes?: string
}

export interface HttpToolRegisterInput {
  tool_name: string
  description: string
  invocation: HttpToolInvocation
  input_schema: Record<string, unknown>
}

export async function registerHttpTool(connId: string,
                                       input: HttpToolRegisterInput) {
  return mutationRequest<{ tool_id: number; capability: string; status: string }>(
    `/api/admin/mcp/connections/${connId}/tools/manual`,
    { operation: 'mcp_http_tool_register', method: 'POST', body: input },
  )
}

export async function updateHttpToolInvocation(
  toolId: number,
  input: { description: string; invocation: HttpToolInvocation;
           input_schema: Record<string, unknown> },
) {
  return mutationRequest<{ tool_id: number; content_hash: string;
                           version: number; schema_changed: boolean }>(
    `/api/admin/mcp/tools/${toolId}/invocation`,
    { operation: 'mcp_http_tool_invocation_update', method: 'PUT', body: input },
  )
}

export async function reviewMcpTool(toolId: number, input: ReviewInput) {
  return mutationRequest<{ tool_id: number; status: string }>(
    `/api/admin/mcp/tools/${toolId}/review`,
    { operation: 'mcp_tool_review', method: 'POST', body: input },
  )
}

export async function disableMcpTool(toolId: number) {
  return mutationRequest<{ tool_id: number; status: string }>(
    `/api/admin/mcp/tools/${toolId}/disable`,
    { operation: 'mcp_tool_disable', method: 'POST', body: {} },
  )
}

export async function grantMcpTool(
  toolId: number,
  productKey: 'agent' | 'customer_service',
  revoke = false,
) {
  return mutationRequest<{ tool_id: number; product_key: string; granted: boolean }>(
    `/api/admin/mcp/tools/${toolId}/grants`,
    {
      operation: 'mcp_tool_grant',
      method: 'POST',
      body: { product_key: productKey, revoke },
    },
  )
}

export async function testInvokeMcpTool(
  toolId: number,
  arguments_: Record<string, unknown>,
  productKey: 'agent' | 'customer_service' | 'internal' = 'internal',
): Promise<TestInvokeResult> {
  return mutationRequest<TestInvokeResult>(
    `/api/admin/mcp/tools/${toolId}/test-invoke`,
    {
      operation: 'mcp_tool_test_invoke',
      method: 'POST',
      body: { arguments: arguments_, product_key: productKey },
    },
  )
}
