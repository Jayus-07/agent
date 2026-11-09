/**
 * /mcp 连接抽屉「运行健康」真实数据区（Batch 5-3，决策 #16）：
 * 自动巡检状态/退避节奏 + 健康事件时间线 + 立即探活（与管理端 /test 同口径）。
 * 故障模拟四按钮保持置灰壳位——那是演练/评测能力，不属健康监测本体。
 * （自 page.tsx 拆出，纯移动。）
 */
import { useState } from 'react'
import Link from 'next/link'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import StatusChip, { type StatusTone } from '@/components/layout/StatusChip'
import { AssetActionButton } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import { useToast } from '@/components/shared/Toast'
import { getMcpHealthOverview, testMcpConnection, type McpConnection } from '@/api/mcp'
import { errMessage } from '@/api/client'

export function ConnectionHealthSection({ conn }: { conn: McpConnection }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [probing, setProbing] = useState(false)
  // 健康总览走 react-query（P2-6 样板）：queryKey 带 conn.id——切换连接时
  // 重新拉取（与原 useEffect 依赖 conn.id 重放同语义）；写后失效重取。
  const healthQuery = useQuery({
    queryKey: ['admin', 'mcp-health', conn.id],
    queryFn: () => getMcpHealthOverview(),
    select: (r) => r.connections.find((c) => c.id === conn.id) ?? null,
  })
  const health = healthQuery.data ?? null
  const loading = healthQuery.isPending
  const error = healthQuery.error ? errMessage(healthQuery.error) : null

  const onProbe = async () => {
    setProbing(true)
    try {
      const result = await testMcpConnection(conn.id)
      if (result.ok) {
        toast.success(`探活成功（${result.elapsed_ms}ms）`)
      } else {
        toast.error(`探活失败：${result.error ?? '连接不可达'}`)
      }
      void queryClient.invalidateQueries({ queryKey: ['admin', 'mcp-health', conn.id] })
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '探活请求失败')
    } finally {
      setProbing(false)
    }
  }

  if (loading) {
    return <div className="rounded-lg border border-border-subtle bg-white p-6 text-center text-[12px] text-text-muted">加载健康数据中…</div>
  }
  if (error) {
    return <ErrorState message={error} onRetry={() => void healthQuery.refetch()} />
  }
  if (!health) {
    return (
      <div className="rounded-lg border border-border-subtle bg-white p-5 text-[12px] text-text-secondary">
        该连接当前未启用——启用后自动纳入健康巡检（非副作用探活 + 断线退避重连）。
      </div>
    )
  }

  const statusMeta: { label: string; tone: StatusTone } = health.status === 'connected'
    ? { label: '健康', tone: 'success' }
    : health.status === 'error' ? { label: '异常', tone: 'danger' }
      : { label: health.status, tone: 'neutral' }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-surface-base px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-medium text-text-primary">自动巡检</span>
            <StatusChip tone={statusMeta.tone}>{statusMeta.label}</StatusChip>
            {health.probe_fail_count > 0 && (
              <StatusChip tone="warn">连续失败 {health.probe_fail_count} 次</StatusChip>
            )}
          </div>
          <div className="mt-0.5 text-[11px] leading-relaxed text-text-muted">
            非副作用探活（{health.transport === 'http_api' ? 'HEAD 可达性' : 'MCP 握手 + tools/list'}）
            · 基础周期 {health.probe_interval_seconds}s，失败按指数退避（上限 ×16），探活成功自动恢复
            {health.next_probe_in_seconds > 0 && ` · 下次探活约 ${Math.ceil(health.next_probe_in_seconds / 60)} 分钟后`}
          </div>
        </div>
        <AssetActionButton onClick={onProbe} disabled={probing} data-testid="health-probe-btn">
          {probing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          立即探活
        </AssetActionButton>
      </div>

      {health.last_error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50/60 px-3 py-2 text-[12px] text-red-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span className="break-all" title={health.last_error}>{health.last_error}</span>
        </div>
      )}

      <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2.5 text-[11px] text-text-muted">
        影响工具：发现 {health.tool_count} 个，其中审核生效 {health.approved_count} 个——
        连接异常时动态工具调用会以明确错误失败，恢复后无需重新审核。
        <Link href="/tools" className="ml-1 text-accent hover:underline">前往 Tool 治理</Link>
      </div>

      <div className="rounded-lg border border-border-subtle">
        <div className="border-b border-border-subtle bg-bg-elevated/60 px-3 py-2 text-[12px] font-medium text-text-primary">
          健康事件（最近 {health.recent_events.length} 条）
        </div>
        {health.recent_events.length === 0 ? (
          <div className="px-3 py-4 text-center text-[12px] text-text-muted">
            暂无事件——后台巡检周期内将产生第一条记录
          </div>
        ) : (
          <ul className="divide-y divide-black/[0.04]">
            {health.recent_events.map((event, index) => (
              <li key={`${event.created_at}-${index}`} className="flex items-start gap-2 px-3 py-2 text-[12px]">
                {event.ok
                  ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-emerald-600" />
                  : <AlertTriangle size={14} className="mt-0.5 shrink-0 text-red-500" />}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={event.ok ? 'text-emerald-700' : 'text-red-600'}>
                      {event.ok ? '正常' : '异常'}
                    </span>
                    <span className="text-[11px] text-text-muted">
                      {new Date(event.created_at).toLocaleString()} · {event.elapsed_ms}ms ·{' '}
                      {event.source === 'monitor' ? '自动巡检' : '手动探活'}
                    </span>
                  </div>
                  {!event.ok && event.error && (
                    <div className="mt-0.5 break-all text-[11px] text-text-muted" title={event.error}>
                      {event.error}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 故障模拟：演练/评测能力，保持置灰壳位（非健康监测本体） */}
      <div className="grid grid-cols-2 gap-2">
        {['模拟断线', '恢复网络', '模拟 Schema 漂移', '检查并重连'].map((label) => (
          <button key={label} type="button" disabled data-stub="demo"
            title="故障模拟能力建设中，暂不可操作"
            className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 text-[12px] text-text-muted disabled:cursor-not-allowed disabled:opacity-50">
            {label}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-text-muted">
        巡检为只读探活：不会自动发现工具、不会改动审核状态与启用开关；连接异常期间调用以明确错误失败。
      </p>
    </div>
  )
}
