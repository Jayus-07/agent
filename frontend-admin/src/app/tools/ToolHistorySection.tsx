/**
 * /tools 动态工具详情抽屉的「历史」：HTTP API 工具的 invocation 版本对比。
 * （自 page.tsx 拆出，纯移动。）
 */
import type { McpTool } from '@/api/mcp'

export function ToolHistorySection({ tool }: { tool: McpTool }) {
  if (tool.conn_transport !== 'http_api' || (tool.snapshots?.length ?? 0) <= 1) {
    return (
      <div className="text-[12px] text-text-muted">
        暂无可对比的调用定义版本；HTTP API 工具在「编辑调用」保存新版本后，这里展示最近 3 版对比。
      </div>
    )
  }
  return (
    <div className="space-y-2 text-[12px]">
      <div className="text-text-muted">调用定义版本对比（最近 3 版；新版漂移需重审）</div>
      <div className="grid gap-2">
        {(tool.snapshots ?? []).slice(0, 3).map((snap, idx) => (
          <div key={`${snap.discovery_version}-${idx}`}
            className={`rounded border p-2 font-mono text-[11px] ${
              idx === 0 ? 'border-accent/50 bg-accent/5' : 'border-border-subtle'
            }`}>
            <div className="mb-1 flex items-center gap-2">
              <span className="font-medium">v{snap.discovery_version}</span>
              {idx === 0 && (
                <span className="rounded bg-accent/10 px-1 text-[10px] text-accent">当前</span>
              )}
              <span className="text-text-muted">hash {snap.content_hash.slice(0, 8)}</span>
              <span className="text-text-muted">
                {new Date(snap.discovered_at).toLocaleString()}
              </span>
            </div>
            {snap.invocation ? (
              <pre className="whitespace-pre-wrap break-all">
                {JSON.stringify(snap.invocation, null, 0)}
              </pre>
            ) : (
              <div className="text-text-muted">（该版本未记录调用定义）</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
