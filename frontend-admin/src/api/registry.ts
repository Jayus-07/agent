/**
 * 能力治理 API（只读，B13 管理端 /agents、/skills 页数据源）
 *
 * 后端：
 *   - backend/app/api/routes/agents.py        → GET /agents（Agent 节点总览）
 *   - backend/app/api/routes/capabilities.py  → GET /capabilities（能力清单对账）
 *   - backend/app/api/routes/mcp.py           → GET /mcp/servers、/mcp/tools（复用）
 * 全部为只读接口，前端不做任何写操作。
 */

import { request } from '@/lib/fetcher'

// ── Agent 节点总览 ──────────────────────────────────────

export type AgentKind = 'orchestration' | 'skill' | 'domain_graph'

export interface AgentNode {
  name: string
  label: string
  kind: AgentKind
  /** 仅 skill 节点有值：该 Skill 注册的能力 */
  capabilities?: string[]
  /** 仅 domain_graph 节点有值：路由模式标识（内部调度契约，永久不改名） */
  route_mode?: string
  /**
   * 仅 domain_graph 子流图有值：所属顶级域的 route_mode（顶级域图为 null）。
   * 归属三元组由后端域图注册表派生，展示层不自行推断。
   */
  domain?: string | null
  /** 仅 domain_graph 子流图有值：顶级域展示标签（父图 label） */
  domain_label?: string | null
  /** 仅 domain_graph 子流图有值：子流标识（commerce / booking） */
  subflow?: string | null
}

export interface AgentsOverview {
  count: number
  summary: Partial<Record<AgentKind, number>>
  agents: AgentNode[]
}

export function getAgents(): Promise<AgentsOverview> {
  return request<AgentsOverview>('/api/agents')
}

// ── Capability 清单 ────────────────────────────────────

export interface CapabilityRow {
  name: string
  skill: string
  routed: boolean
  /** 负责人团队（manifest owner 字段，治理对账） */
  owner: string
  rule_keywords: string[]
  examples: string[]
  /** routed:false 时的不对用户开放原因 */
  reason: string
  /** manifest 声明 ↔ Skill 注册表对账（false = 漂移信号） */
  registered: boolean
}

export interface WorkflowRow {
  name: string
  examples: string[]
}

/** GET /workflows 返回的运行时注册元数据；不包含运行输出或输入。 */
export interface WorkflowCatalogEntry {
  name: string
  description: string
  objects: string[]
  actions: string[]
  examples: string[]
  default_kbs: string[]
}

export interface WorkflowCatalogResponse {
  workflows: WorkflowCatalogEntry[]
  total: number
}

export interface SkillRow {
  name: string
  description: string
  capabilities: string[]
}

export interface CapabilitiesOverview {
  count: number
  routed_count: number
  capabilities: CapabilityRow[]
  workflows: WorkflowRow[]
  skills: SkillRow[]
}

export function getCapabilities(): Promise<CapabilitiesOverview> {
  return request<CapabilitiesOverview>('/api/capabilities')
}

/** GET /workflows — Workflow 注册元数据，只读；不请求无资源授权保证的 runs 端点。 */
export function getWorkflowCatalog(): Promise<WorkflowCatalogResponse> {
  return request<WorkflowCatalogResponse>('/api/workflows')
}

// ── MCP Server / Tool（复用既有只读接口） ────────────────
// 命名注意：这里是 /api/mcp/*（mcp_servers/manager 的**内部** MCP server
// 运行时视图，/skills 页展示注册情况）；ADR-0011 外部动态工具的治理
// 视图在 @/api/mcp（/api/admin/mcp/*）。Runtime 前缀避免两套 McpTool
// 同名混淆——历史上两处同名不同义，误改会造成跨视图类型错配。

export interface RuntimeMcpServer {
  name: string
  description: string
  tool_count: number
}

export interface RuntimeMcpTool {
  server: string
  name: string
  description: string
  parameters: Record<string, unknown>
}

export function getRuntimeMcpServers(): Promise<{ servers: RuntimeMcpServer[] }> {
  return request<{ servers: RuntimeMcpServer[] }>('/api/mcp/servers')
}

export function getRuntimeMcpTools(): Promise<{ count: number; tools: RuntimeMcpTool[] }> {
  return request<{ count: number; tools: RuntimeMcpTool[] }>('/api/mcp/tools')
}
