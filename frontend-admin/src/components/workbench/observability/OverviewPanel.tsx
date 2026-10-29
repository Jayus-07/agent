'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { RefreshCw } from 'lucide-react'
import { getTraceStats, listTracesPage, listActiveTraces, type AuthorizedTraceStats } from '@/api/observability'
import type { TraceRecord } from '@/types/trace'
import { formatCost, formatRelative } from '@/types/trace'
import {
  filterRuntimeRecords,
  getRunStatusLabel,
  getRunStatus,
  RUNTIME_WINDOW_LABELS,
  summarizeRuntimeSample,
  type RuntimeWindow,
} from '@/lib/observability/runtimeStatus'
import { deriveDomainLabel } from '@/lib/observability/runKind'

/**
 * 运行总览（2026-10-30 按模拟页重构，不复用旧 RuntimeOverview）。
 *
 * 保留的不可消失契约：stats 授权口径与双时间戳、10 项终态全部可点击进入筛选、
 * running 实时计数来自授权活跃端点（不以历史样本推断）、失败全屏态与过期标记、
 * 「后端契约缺口」披露块。新增：业务域分布（基于最近 200 条授权样本聚合，
 * 服务端 stats 尚无 domain 维度——口径已在面板标注，pushdown 任务补齐后切换）。
 */

const WINDOWS: RuntimeWindow[] = ['15m', '1h', '6h', '24h']
const WINDOW_VALUES = new Set<string>(WINDOWS)
const WINDOW_HOURS: Record<RuntimeWindow, number> = { '15m': 0.25, '1h': 1, '6h': 6, '24h': 24 }

const TERMINAL_STATUSES: Array<keyof AuthorizedTraceStats['counts']> = [
  'success', 'failed', 'error', 'degraded', 'partial', 'timeout', 'rejected', 'cancelled', 'unknown',
]

function windowFromUrl(value: string | null): RuntimeWindow {
  return value && WINDOW_VALUES.has(value) ? (value as RuntimeWindow) : '24h'
}

function durationLabel(value: number | null): string {
  if (value === null) return '未采集'
  return value >= 1000 ? `${(value / 1000).toFixed(2)} 秒` : `${value} 毫秒`
}

function statusFilterHref(range: RuntimeWindow, status: string): string {
  const params = new URLSearchParams({ tab: 'traces', range, status })
  return `/observability/monitoring?${params.toString()}`
}

export default function OverviewPanel() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const range = windowFromUrl(searchParams.get('range'))
  const [traces, setTraces] = useState<TraceRecord[]>([])
  const [stats, setStats] = useState<AuthorizedTraceStats | null>(null)
  const [activeCount, setActiveCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const hasFetched = useRef(false)
  const requestSequence = useRef(0)

  const loadTraces = useCallback(async () => {
    const requestId = ++requestSequence.current
    if (hasFetched.current) setRefreshing(true)
    else setLoading(true)
    try {
      const until = new Date()
      const since = new Date(until.getTime() - WINDOW_HOURS[range] * 60 * 60_000)
      const [result, authorizedStats, active] = await Promise.all([
        listTracesPage({
          limit: 200,
          offset: 0,
          since: since.toISOString(),
          until: until.toISOString(),
          excludeRunning: true,
        }),
        getTraceStats(WINDOW_HOURS[range]),
        // 运行中走授权活跃端点；失败时计数置 null，不用历史样本推断
        listActiveTraces().catch(() => null),
      ])
      if (requestId !== requestSequence.current) return
      setTraces(result.traces)
      setStats(authorizedStats)
      setActiveCount(Array.isArray(active) ? active.length : null)
      hasFetched.current = true
      setLastFetchedAt(new Date().toISOString())
      setLoadFailed(false)
    } catch {
      if (requestId === requestSequence.current) setLoadFailed(true)
    } finally {
      if (requestId === requestSequence.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [range])

  useEffect(() => {
    void loadTraces()
    return () => { requestSequence.current += 1 }
  }, [loadTraces])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = setInterval(() => { void loadTraces() }, 30_000)
    return () => clearInterval(timer)
  }, [autoRefresh, loadTraces])

  const sampleNowMs = Date.now()
  const inWindow = useMemo(() => filterRuntimeRecords(traces, range, sampleNowMs), [range, traces])
  const sampleSummary = useMemo(() => summarizeRuntimeSample(traces, range, sampleNowMs), [range, traces])

  const recent = useMemo(
    () => [...inWindow].sort((a, b) => b.timestamp.localeCompare(a.timestamp)).slice(0, 8),
    [inWindow],
  )
  const recentErrors = useMemo(
    () => inWindow
      .filter((t) => getRunStatus(t) === 'error' || getRunStatus(t) === 'failed')
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
      .slice(0, 5),
    [inWindow],
  )
  const domainRows = useMemo(() => {
    const byDomain = new Map<string, { total: number; errors: number }>()
    inWindow.forEach((t) => {
      const key = deriveDomainLabel(t)
      const row = byDomain.get(key) ?? { total: 0, errors: 0 }
      row.total += 1
      const st = getRunStatus(t)
      if (st === 'error' || st === 'failed') row.errors += 1
      byDomain.set(key, row)
    })
    return [...byDomain.entries()].sort((a, b) => b[1].total - a[1].total)
  }, [inWindow])

  const changeWindow = (next: RuntimeWindow) => {
    const params = new URLSearchParams(searchParams.toString())
    params.set('range', next)
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }

  const detailHref = (trace: TraceRecord) => {
    const params = new URLSearchParams({ returnTo: `${pathname}?${searchParams.toString()}` })
    return `/observability/traces/${encodeURIComponent(trace.id)}?${params.toString()}`
  }

  if (loading && !lastFetchedAt) {
    return <div className="p-6 text-sm text-text-secondary" role="status">正在读取已授权的运行记录…</div>
  }

  if (loadFailed && !lastFetchedAt) {
    return (
      <section className="m-4 rounded-lg border border-red-200 bg-red-50 p-4 sm:m-6" role="alert">
        <h2 className="font-semibold text-red-800">运行数据暂不可用</h2>
        <p className="mt-1 text-sm text-red-700">读取 Trace 服务失败。没有使用缓存统计或模拟数据。</p>
        <button type="button" onClick={() => void loadTraces()} className="mt-3 min-h-10 rounded-md border border-red-300 bg-white px-3 text-sm font-medium text-red-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500">重试读取</button>
      </section>
    )
  }

  const totalTerminal = stats ? TERMINAL_STATUSES.reduce((sum, key) => sum + (stats.counts[key] ?? 0), 0) : 0

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">运行总览</h2>
          <p className="mt-1 max-w-3xl text-sm text-text-secondary">按业务域分层看健康：失败优先，终态全部可点击进入执行记录筛选。</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="inline-flex min-h-10 items-center gap-1.5 text-xs text-text-secondary">
            <input type="checkbox" aria-label="自动刷新" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="accent-accent" />
            自动刷新 30s
          </label>
          <button type="button" onClick={() => void loadTraces()} disabled={refreshing} className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border-default bg-white px-3 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-60">
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} aria-hidden="true" />{refreshing ? '刷新中…' : '刷新数据'}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2" aria-label="统计时间范围">
        {WINDOWS.map((w) => (
          <button key={w} type="button" aria-pressed={range === w} onClick={() => changeWindow(w)} className={`min-h-10 rounded-md border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${range === w ? 'border-accent bg-violet-50 font-medium text-accent' : 'border-border-subtle bg-white text-text-secondary hover:bg-bg-hover'}`}>
            {RUNTIME_WINDOW_LABELS[w]}
          </button>
        ))}
      </div>

      {loadFailed && lastFetchedAt && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">
          本次刷新失败，以下为上次读取于 {new Date(lastFetchedAt).toLocaleString('zh-CN')} 的数据，当前已标记为过期。
        </div>
      )}

      <div className="rounded-md border border-border-subtle bg-white px-4 py-3 text-xs leading-5 text-text-secondary">
        <p>数据源：已授权分页 Trace API 与 <code>GET /api/observability/traces/stats</code>、<code>GET /traces/active</code>（实时）· 当前时间范围：{RUNTIME_WINDOW_LABELS[range]}</p>
        <p>统计范围为 TraceStore 最近最多 {stats?.retention_limit ?? 5000} 条留存记录中的授权记录，不代表全量历史；页面查询于 {lastFetchedAt ? new Date(lastFetchedAt).toLocaleString('zh-CN') : '尚未成功'}；服务端统计时间 {stats?.observed_at ? new Date(stats.observed_at).toLocaleString('zh-CN') : '尚不可用'}。</p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Link href={statusFilterHref(range, 'error')} className="min-h-24 rounded-md border border-red-200 bg-red-50 px-4 py-3 hover:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
          <span className="block text-sm text-red-700">执行错误</span>
          <strong className="mt-2 block font-mono text-2xl tabular-nums text-red-700">{stats?.counts.error ?? 0}</strong>
          <span className="mt-1 block text-xs text-red-500">点击进入错误 Trace 筛选</span>
        </Link>
        <div className="min-h-24 rounded-md border border-border-subtle bg-white px-4 py-3">
          <span className="block text-sm text-text-secondary">成功率</span>
          <strong className="mt-2 block font-mono text-2xl tabular-nums text-text-primary">{stats ? `${(stats.success_rate * 100).toFixed(1)}%` : '未采集'}</strong>
          <span className="mt-1 block text-xs text-text-muted">{stats?.counts.success ?? 0} 成功 / {totalTerminal} 条非运行记录</span>
        </div>
        <div className="min-h-24 rounded-md border border-border-subtle bg-white px-4 py-3">
          <span className="block text-sm text-text-secondary">P50 / P95 耗时</span>
          <strong className="mt-2 block font-mono text-xl tabular-nums text-text-primary">{durationLabel(stats?.p50_duration_ms ?? null)} / {durationLabel(stats?.p95_duration_ms ?? null)}</strong>
          <span className="mt-1 block text-xs text-text-muted">授权留存记录中的已知终态</span>
        </div>
        <div className="min-h-24 rounded-md border border-violet-200 bg-violet-50/60 px-4 py-3">
          <span className="block text-sm text-violet-800">运行中（实时）</span>
          <strong className="mt-2 block font-mono text-2xl tabular-nums text-violet-900">{activeCount === null ? '未采集' : activeCount}</strong>
          <span className="mt-1 block text-xs text-violet-700">授权活跃端点实时计数</span>
        </div>
        <div className="min-h-24 rounded-md border border-border-subtle bg-white px-4 py-3">
          <span className="block text-sm text-text-secondary">样本费用（CNY）</span>
          <strong className="mt-2 block font-mono text-xl tabular-nums text-text-primary">{sampleSummary.costCny === null ? '未采集' : formatCost(sampleSummary.costCny)}</strong>
          <span className="mt-1 block text-xs text-text-muted">{sampleSummary.costRecordCount}/{sampleSummary.total} 条有 CNY 费用；不换算旧 USD 字段</span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-md border border-border-subtle bg-white p-4">
          <h3 className="mb-2 text-[13px] font-medium text-text-primary">业务域分布（{RUNTIME_WINDOW_LABELS[range]} · 最近 200 条授权样本聚合）</h3>
          {domainRows.length === 0 ? (
            <p className="py-4 text-sm text-text-muted">当前时间范围没有可确认的终态记录。</p>
          ) : (
            <>
              {domainRows.map(([domain, row]) => (
                <div key={domain} className="mb-1.5 flex items-center gap-2" aria-label={`${domain} ${row.total} 条，错误 ${row.errors}`}>
                  <span className="w-28 shrink-0 truncate text-xs text-text-secondary" title={domain}>{domain}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full bg-violet-400" style={{ width: `${Math.max(2, (row.total / (inWindow.length || 1)) * 100)}%` }} />
                  </div>
                  <span className="w-10 text-right font-mono text-xs tabular-nums text-text-secondary">{row.total}</span>
                  <span className={`w-16 text-right text-[11px] tabular-nums ${row.errors > 0 ? 'text-red-600' : 'text-text-muted'}`}>{row.errors} 错</span>
                </div>
              ))}
              <p className="mt-2 text-[11px] text-text-muted">主图=1 个 LangGraph 企业助手图，域分支由 runtime_domain tag 区分；服务端 stats 尚无 domain 维度，本区块为样本聚合口径（pushdown 任务补齐后切换）。</p>
            </>
          )}
        </section>

        <section className="rounded-md border border-border-subtle bg-white p-4">
          <h3 className="mb-2 text-[13px] font-medium text-text-primary">终态分布（全部可点击筛选）</h3>
          {TERMINAL_STATUSES.map((key) => {
            const count = stats?.counts[key] ?? 0
            const tone = key === 'success' ? 'bg-emerald-400' : key === 'failed' || key === 'error' ? 'bg-red-400' : key === 'degraded' || key === 'timeout' || key === 'partial' ? 'bg-amber-300' : 'bg-slate-300'
            return (
              <Link key={key} href={statusFilterHref(range, key)} className="mb-1.5 flex items-center gap-2 rounded px-1 py-0.5 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                <span className="w-20 shrink-0 text-xs text-text-secondary">{getRunStatusLabel(key)}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                  <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.max(2, (count / (totalTerminal || 1)) * 100)}%` }} />
                </div>
                <span className="w-12 text-right font-mono text-xs tabular-nums text-text-secondary">{count}</span>
              </Link>
            )
          })}
        </section>
      </div>

      {recentErrors.length > 0 && (
        <section className="rounded-md border border-red-200 bg-red-50/60 p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <h3 className="text-[13px] font-medium text-red-700">最近错误（点击进入错误筛选）</h3>
            <Link href={statusFilterHref(range, 'error')} className="text-xs font-medium text-accent underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">全部错误</Link>
          </div>
          {recentErrors.map((t) => {
            const code = typeof t.error?.code === 'string' && t.error.code.trim() ? t.error.code : '未采集'
            const msg = typeof t.error?.message === 'string' ? t.error.message : ''
            return (
              <Link key={t.id} href={statusFilterHref(range, 'error')} className="mb-1 flex items-center gap-2 rounded px-1 py-0.5 text-xs hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                <span className="w-36 shrink-0 font-mono text-red-700">{code}</span>
                <span className="min-w-0 flex-1 truncate text-text-secondary" title={msg}>{msg || '错误消息未采集'}</span>
                <span className="shrink-0 font-mono text-text-muted">{t.workflow_name || '未记录'} · {formatRelative(t.timestamp)}</span>
              </Link>
            )
          })}
        </section>
      )}

      <section className="rounded-md border border-border-subtle bg-white">
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle px-4 py-3">
          <h3 className="font-semibold text-text-primary">最近运行</h3>
          <Link href={statusFilterHref(range, 'all')} className="min-h-10 inline-flex items-center text-sm font-medium text-accent underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">查看执行记录</Link>
        </div>
        {recent.length === 0 ? (
          <p className="px-4 py-6 text-sm text-text-secondary">最近 200 条授权样本中，所选时间范围没有可确认的终态记录；历史列表的 running 状态不会当作实时运行。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px] border-collapse text-left text-sm">
              <thead className="bg-bg-elevated text-xs text-text-secondary">
                <tr><th scope="col" className="px-4 py-2.5 font-medium">Trace ID</th><th scope="col" className="px-4 py-2.5 font-medium">Agent / Workflow</th><th scope="col" className="px-4 py-2.5 font-medium">状态</th><th scope="col" className="px-4 py-2.5 font-medium">耗时</th><th scope="col" className="px-4 py-2.5 font-medium">开始时间</th></tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {recent.map((t) => {
                  const st = getRunStatus(t)
                  return (
                    <tr key={t.id} className="hover:bg-bg-elevated">
                      <td className="px-4 py-3"><Link href={detailHref(t)} title={t.id} className="font-mono text-accent underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">{t.id.slice(0, 12)}</Link></td>
                      <td className="max-w-64 truncate px-4 py-3 text-text-primary" title={t.workflow_name || '未记录'}>{t.workflow_name || '未记录'}</td>
                      <td className="px-4 py-3">{getRunStatusLabel(st)}</td>
                      <td className="px-4 py-3 font-mono tabular-nums text-text-secondary">{t.duration_ms > 0 ? durationLabel(t.duration_ms) : '未结束'}</td>
                      <td className="px-4 py-3 text-text-secondary" title={new Date(t.timestamp).toLocaleString('zh-CN')}>{formatRelative(t.timestamp)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
        <h3 className="font-semibold">后端契约缺口</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>列表 DTO 未下发 workflow_kind（执行类型徽标暂按 workflow_name 前端映射，pushdown 任务补齐后切换）。</li>
          <li>Run 级待审批状态未随 Trace 返回；不会把运行中当作待审批。</li>
          <li>降级计数只认明确的 <code>metadata.business_outcome=degraded</code>；当前成功记录没有覆盖完整的降级观测率。</li>
          <li>Span DTO 无 HTTP status code；详情页不展示 HTTP 响应口径。</li>
          <li>TraceStore 留存上限为 {stats?.retention_limit ?? 5000} 条；统计仅覆盖这批留存记录中的授权数据。</li>
        </ul>
      </section>
    </div>
  )
}
