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
  /** 仅 domain_graph 节点有值：路由模式标识 */
  route_mode?: string
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

// ── MCP Server / Tool（复用既有只读接口） ────────────────

export interface McpServer {
  name: string
  description: string
  tool_count: number
}

export interface McpTool {
  server: string
  name: string
  description: string
  parameters: Record<string, unknown>
}

export function getMcpServers(): Promise<{ servers: McpServer[] }> {
  return request<{ servers: McpServer[] }>('/api/mcp/servers')
}

export function getMcpTools(): Promise<{ count: number; tools: McpTool[] }> {
  return request<{ count: number; tools: McpTool[] }>('/api/mcp/tools')
}
