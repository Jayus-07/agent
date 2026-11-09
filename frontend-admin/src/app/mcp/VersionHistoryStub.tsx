/**
 * /mcp 连接抽屉「版本历史」置灰壳位（自 page.tsx 拆出，纯移动）：
 * 配置版本时间线属下一批，展示真实当前版本锚点。
 */
import type { McpConnection } from '@/api/mcp'

export function VersionHistoryStub({ conn }: { conn: McpConnection }) {
  return (
    <div data-stub="demo" className="space-y-3">
      <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-6 text-center text-[12px] text-text-muted">
        配置版本时间线属下一批：当前仅显示最新发现版本，历史版本追溯建设中。
      </div>
      <div className="rounded-lg border border-border-subtle px-3 py-2.5 text-[12px]">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-text-primary">当前发现版本 v{conn.discovery_version}</span>
          <span className="text-text-muted">
            最后同步 {conn.last_synced_at ? new Date(conn.last_synced_at).toLocaleString() : '—'}
          </span>
        </div>
        {conn.updated_at && (
          <div className="mt-0.5 text-[11px] text-text-muted">
            连接配置更新于 {new Date(conn.updated_at).toLocaleString()}（by {conn.updated_by || '—'}）
          </div>
        )}
      </div>
    </div>
  )
}
