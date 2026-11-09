/**
 * /tools 页来源徽章（自 page.tsx 拆出，纯移动）。
 * 静态契约的数据源徽章与动态工具的来源徽章共用一个出口。
 */
import type { ToolContractEntry } from '@/api/governance'
import type { McpToolSummary } from '@/api/mcp'

const DATA_SOURCE_META: Record<string, { label: string; cls: string }> = {
  mcp: { label: 'MCP', cls: 'bg-violet-100 text-violet-700' },
  rest: { label: '外部', cls: 'bg-sky-100 text-sky-700' },
}

/** 数据源徽章：MCP 带「上游·开关」tooltip；rest 带 provider；internal 不显示（避免全表噪音）。 */
export function DataSourceBadge({ ds }: { ds: NonNullable<ToolContractEntry['data_source']> }) {
  if (ds.type === 'internal') return null
  const meta = DATA_SOURCE_META[ds.type]
  if (!meta) return null
  const tip =
    ds.type === 'mcp'
      ? `外部 MCP server 数据源：${ds.provider ?? ''} · 上游工具 ${ds.upstream_tool ?? '—'}（开关 ${ds.switch_env ?? '—'}，关闭时该 Tool 明确报未配置）`
      : `外部网络 API：${ds.provider ?? ''}`
  return (
    <span className={`rounded px-1 py-0.5 text-[10px] font-medium whitespace-nowrap ${meta.cls}`} title={tip}>
      {meta.label}·{ds.provider}
    </span>
  )
}

/** 动态工具来源徽章：MCP（Streamable HTTP 发现）/ HTTP API（手工登记），附连接名。 */
export function DynamicSourceBadge({ tool }: { tool: McpToolSummary }) {
  const isHttp = tool.conn_transport === 'http_api'
  return (
    <span
      className={`rounded px-1 py-0.5 text-[10px] font-medium whitespace-nowrap align-middle ${
        isHttp ? 'bg-sky-100 text-sky-700' : 'bg-violet-100 text-violet-700'
      }`}
      title={isHttp
        ? `HTTP API 工具：${tool.invocation?.method ?? ''} ${tool.invocation?.path ?? ''}`
        : 'MCP 工具（Streamable HTTP 发现）'}
    >
      {isHttp ? 'HTTP API' : 'MCP'}
      {tool.connection_name ? `·${tool.connection_name}` : ''}
    </span>
  )
}
