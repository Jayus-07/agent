/**
 * /mcp 连接卡片（自 page.tsx 拆出，纯移动）：
 * 状态 / 类型 / 工具数 / 最近检查 + 查看详情。
 */
import StatusChip, { type StatusTone } from '@/components/layout/StatusChip'
import { ASSET_SURFACE, AssetActionButton } from '@/components/layout/AssetPageShell'
import type { McpConnection } from '@/api/mcp'
import { CONN_STATUS_META, TransportBadge } from './badges'

export function ConnectionCard({ conn, toolCount, onOpen }: {
  conn: McpConnection
  toolCount: number
  onOpen: (conn: McpConnection) => void
}) {
  const meta = CONN_STATUS_META[conn.status] ?? { label: conn.status, tone: 'neutral' as StatusTone }
  return (
    <div className={ASSET_SURFACE + ' flex flex-col gap-2 p-4'}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[13px] font-semibold text-text-primary">{conn.name}</span>
            <TransportBadge transport={conn.transport} />
          </div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-text-muted" title={conn.base_url}>
            {conn.base_url}
          </div>
        </div>
        <StatusChip tone={meta.tone}>{meta.label}</StatusChip>
      </div>
      {conn.last_error && (
        <div className="truncate text-[11px] text-red-600" title={conn.last_error}>{conn.last_error}</div>
      )}
      <div className="mt-auto flex items-end justify-between gap-2 pt-1">
        <div className="text-[11px] leading-relaxed text-text-muted">
          <div>发现工具 <span className="font-mono text-text-secondary">{toolCount}</span> 个 · v{conn.discovery_version}</div>
          <div>最近检查 {conn.last_synced_at ? new Date(conn.last_synced_at).toLocaleString() : '—'}</div>
          <div>
            最近探活 {conn.last_checked_at ? new Date(conn.last_checked_at).toLocaleString() : '—'}
            {(conn.probe_fail_count ?? 0) > 0 && (
              <span className="ml-1 text-amber-600">连续失败 {conn.probe_fail_count}</span>
            )}
          </div>
        </div>
        <AssetActionButton onClick={() => onOpen(conn)}>查看详情</AssetActionButton>
      </div>
    </div>
  )
}
