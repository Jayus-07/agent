'use client'

/**
 * 工具执行约束（Batch 3，预算治理页只读区块）。
 *
 * 数据源 GET /api/admin/tools/constraints：每个 capability 的超时 /
 * 每请求调用上限 / 并发隔离 / 重试 / 熔断 / 限流。静态能力来自策略表，
 * 动态 mcp.* 来自审核 governance——约束修改走 /mcp 审核流，此处只读。
 */
import { useQuery } from '@tanstack/react-query'
import { Wrench } from 'lucide-react'
import { request } from '@/api/client'

interface ToolConstraint {
  capability: string
  source: 'static' | 'dynamic'
  operation: string
  risk_level: string
  timeout_ms: number
  max_calls_per_request: number
  bulkhead_limit: number
  retries: number
  circuit_breaker: boolean
  rate_limit_enabled: boolean
  rate_limit_requests: number
  rate_limit_window_seconds: number
}

export default function ToolConstraintsSection() {
  const { data, isFetching, error, refetch } = useQuery({
    queryKey: ['tool-constraints'],
    queryFn: () => request<{ count: number; constraints: ToolConstraint[] }>(
      '/api/admin/tools/constraints'),
    staleTime: 30_000,
  })

  return (
    <section className="rounded-xl border border-border-subtle bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-[13px] font-semibold text-text-primary">
          <Wrench size={14} className="text-blue-500" aria-hidden />
          工具执行约束
          <span className="text-[11px] font-normal text-text-muted">
            {data ? `${data.count} 个能力` : '加载中…'}
            （只读：动态工具的约束修改走 MCP 能力中心审核流）
          </span>
        </h2>
        <button onClick={() => void refetch()} disabled={isFetching}
          className="text-[11px] text-accent hover:underline disabled:opacity-50">
          {isFetching ? '刷新中…' : '刷新'}
        </button>
      </div>

      {error ? (
        <div className="rounded-lg bg-red-50 px-3 py-2 text-[12px] text-red-700">
          加载失败：{error instanceof Error ? error.message : '请求失败'}
        </div>
      ) : !data ? (
        <div className="py-4 text-center text-[12px] text-text-muted">加载中…</div>
      ) : data.constraints.length === 0 ? (
        <div className="py-4 text-center text-[12px] text-text-muted">无已注册能力</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[12px]">
            <thead>
              <tr className="text-[11px] text-text-muted">
                <th className="px-3 py-2 font-normal">Capability</th>
                <th className="px-2 py-2 font-normal">来源</th>
                <th className="px-2 py-2 font-normal">操作/风险</th>
                <th className="px-2 py-2 font-normal">超时</th>
                <th className="px-2 py-2 font-normal">单请求上限</th>
                <th className="px-2 py-2 font-normal">并发隔离</th>
                <th className="px-2 py-2 font-normal">重试</th>
                <th className="px-2 py-2 font-normal">熔断</th>
                <th className="px-3 py-2 font-normal">限流</th>
              </tr>
            </thead>
            <tbody>
              {data.constraints.map((c) => (
                <tr key={c.capability} className="border-t border-border-subtle">
                  <td className="px-3 py-2 font-mono text-text-primary">{c.capability}</td>
                  <td className="px-2 py-2">
                    <span className={`rounded px-1 py-0.5 text-[10px] font-medium ${
                      c.source === 'dynamic'
                        ? 'bg-violet-100 text-violet-700'
                        : 'bg-slate-100 text-slate-600'
                    }`}>
                      {c.source === 'dynamic' ? '动态' : '静态'}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-text-secondary">
                    {c.operation}/{c.risk_level}
                  </td>
                  <td className="px-2 py-2">{(c.timeout_ms / 1000).toFixed(0)}s</td>
                  <td className="px-2 py-2">{c.max_calls_per_request} 次</td>
                  <td className="px-2 py-2">{c.bulkhead_limit}</td>
                  <td className="px-2 py-2">{c.retries}</td>
                  <td className="px-2 py-2">{c.circuit_breaker ? '开' : '关'}</td>
                  <td className="px-3 py-2">
                    {c.rate_limit_enabled
                      ? `${c.rate_limit_requests} 次/${c.rate_limit_window_seconds}s`
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
