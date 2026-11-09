/**
 * /mcp 连接中心的连接/工具/传输徽章（自 page.tsx 拆出，纯移动）。
 * ConnectionCard、凭据表与连接详情抽屉共用同一状态口径。
 */
import type { StatusTone } from '@/components/layout/StatusChip'

export const CONN_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  never_connected: { label: '未连接', tone: 'neutral' },
  connected: { label: '已连接', tone: 'success' },
  error: { label: '连接异常', tone: 'danger' },
  disabled: { label: '已停用', tone: 'warn' },
}

export const TOOL_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  pending: { label: '待审核', tone: 'warn' },
  approved: { label: '已生效', tone: 'success' },
  rejected: { label: '已拒绝', tone: 'danger' },
  disabled: { label: '已停用', tone: 'neutral' },
  retired: { label: '上游已消失', tone: 'neutral' },
}

const TRANSPORT_META: Record<string, { label: string; badgeCls: string }> = {
  streamable_http: { label: 'MCP · Streamable HTTP', badgeCls: 'bg-violet-100 text-violet-700' },
  http_api: { label: 'HTTP API', badgeCls: 'bg-sky-100 text-sky-700' },
}

export function TransportBadge({ transport }: { transport: string }) {
  const meta = TRANSPORT_META[transport]
  if (!meta) return <span className="rounded bg-slate-100 px-1 py-0.5 text-[10px] font-medium text-slate-600">{transport}</span>
  return (
    <span className={`rounded px-1 py-0.5 text-[10px] font-medium whitespace-nowrap ${meta.badgeCls}`}>
      {meta.label}
    </span>
  )
}
