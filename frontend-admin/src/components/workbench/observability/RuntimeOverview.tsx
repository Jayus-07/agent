'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getTraceStats, listTracesPage, listActiveTraces, type AuthorizedTraceStats } from '@/api/observability'
import type { TraceRecord } from '@/types/trace'
import {
  getRunStatus,
  getRunStatusLabel,
  getRunStatusTone,
  filterRuntimeRecords,
  RUNTIME_WINDOW_LABELS,
  summarizeRuntimeSample,
  type RuntimeWindow,
} from '@/lib/observability/runtimeStatus'
import { formatCost, formatRelative } from '@/types/trace'

const WINDOWS: RuntimeWindow[] = ['15m', '1h', '6h', '24h']
const WINDOW_VALUES = new Set<string>(WINDOWS)
const WINDOW_HOURS: Record<RuntimeWindow, number> = { '15m': 0.25, '1h': 1, '6h': 6, '24h': 24 }

function windowFromUrl(value: string | null): RuntimeWindow {
  return value && WINDOW_VALUES.has(value) ? value as RuntimeWindow : '24h'
}

function durationLabel(value: number | null): string {
  if (value === null) return '未采集'
  return value >= 1000 ? `${(value / 1000).toFixed(2)} 秒` : `${value} 毫秒`
}

function detailHref(trace: TraceRecord, returnTo: string): string {
  const params = new URLSearchParams({ returnTo })
  return `/observability/traces/${encodeURIComponent(trace.id)}?${params.toString()}`
}

function statusFilterHref(range: RuntimeWindow, status: string): string {
  const params = new URLSearchParams({ tab: 'traces', range, status })
  return `/observability/monitoring?${params.toString()}`
}

export default function RuntimeOverview() {
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
        // 运行中走授权活跃端点（2026-10-30 解锁）；失败时计数置 null，不用历史样本推断
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

  const sampleNowMs = Date.now()
  const inWindow = useMemo(
    () => filterRuntimeRecords(traces, range, sampleNowMs),
    [range, traces],
  )
  const sampleSummary = useMemo(
    () => summarizeRuntimeSample(traces, range, sampleNowMs),
    [range, traces],
  )

  const changeWindow = (next: RuntimeWindow) => {
    const params = new URLSearchParams(searchParams.toString())
    params.set('range', next)
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }

  const runListUrl = useMemo(() => {
    const params = new URLSearchParams(searchParams.toString())
    params.set('tab', 'traces')
    params.set('range', range)
    params.delete('status')
    return `/observability/monitoring?${params.toString()}`
  }, [range, searchParams])

  const returnTo = useMemo(() => {
    const params = new URLSearchParams(searchParams.toString())
    params.set('tab', 'traces')
    params.set('range', range)
    return `/observability/monitoring?${params.toString()}`
  }, [range, searchParams])

  const recent = useMemo(
    // 列表端点可能返回旧记录的 running 默认状态；没有活跃 Trace 授权查询时，不把它显示为当前执行。
    () => [...inWindow]
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
      .slice(0, 8),
    [inWindow],
  )

  const cards = [
    { label: '记录数', value: String(stats?.total ?? 0), status: 'all', tone: 'text-slate-900' },
    { label: '成功', value: String(stats?.counts.success ?? 0), status: 'success', tone: 'text-emerald-700' },
    { label: '业务失败', value: String(stats?.counts.failed ?? 0), status: 'failed', tone: 'text-red-700' },
    { label: '执行错误', value: String(stats?.counts.error ?? 0), status: 'error', tone: 'text-red-700' },
    { label: '降级', value: String(stats?.counts.degraded ?? 0), status: 'degraded', tone: 'text-amber-700' },
    { label: '部分成功', value: String(stats?.counts.partial ?? 0), status: 'partial', tone: 'text-amber-700' },
    { label: '超时', value: String(stats?.counts.timeout ?? 0), status: 'timeout', tone: 'text-amber-700' },
    { label: '已拒绝', value: String(stats?.counts.rejected ?? 0), status: 'rejected', tone: 'text-orange-700' },
    { label: '已取消', value: String(stats?.counts.cancelled ?? 0), status: 'cancelled', tone: 'text-slate-700' },
    { label: '状态未知', value: String(stats?.counts.unknown ?? 0), status: 'unknown', tone: 'text-slate-600' },
  ]

  if (loading && !lastFetchedAt) {
    return <div className="p-6 text-sm text-text-secondary" role="status">正在读取已授权的运行记录…</div>
  }

  if (loadFailed && !lastFetchedAt) {
    return (
      <section className="m-4 rounded-lg border border-red-200 bg-red-50 p-4 sm:m-6" role="alert">
        <h2 className="font-semibold text-red-800">运行数据暂不可用</h2>
        <p className="mt-1 text-sm text-red-700">读取 Trace 服务失败。没有使用缓存统计或模拟数据。</p>
        <button type="button" onClick={() => void loadTraces()} className="mt-3 min-h-10 rounded-md border border-red-300 bg-white px-3 text-sm font-medium text-red-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500">
          重试读取
        </button>
      </section>
    )
  }

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">运行总览</h2>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
            统计由服务端在当前授权范围内聚合 TraceStore 留存记录；最近运行表仍最多展示 200 条样本。
          </p>
        </div>
        <button type="button" onClick={() => void loadTraces()} disabled={refreshing} className="min-h-10 rounded-md border border-border-default bg-white px-3 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-60">
          {refreshing ? '刷新中…' : '刷新数据'}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2" aria-label="统计时间范围">
        {WINDOWS.map((window) => (
          <button
            key={window}
            type="button"
            aria-pressed={range === window}
            onClick={() => changeWindow(window)}
            className={`min-h-10 rounded-md border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${range === window ? 'border-accent bg-accent-soft font-medium text-accent' : 'border-border-subtle bg-white text-text-secondary hover:bg-bg-hover'}`}
          >
            {RUNTIME_WINDOW_LABELS[window]}
          </button>
        ))}
      </div>

      {loadFailed && lastFetchedAt && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">
          本次刷新失败，以下为上次读取于 {new Date(lastFetchedAt).toLocaleString('zh-CN')} 的数据，当前已标记为过期。
        </div>
      )}

      <div className="rounded-md border border-border-subtle bg-white px-4 py-3 text-xs leading-5 text-text-secondary">
        <p>数据源：已授权分页 Trace API 与 <code>GET /api/observability/traces/stats</code> · 当前时间范围：{RUNTIME_WINDOW_LABELS[range]}</p>
        <p>统计范围为 TraceStore 最近最多 {stats?.retention_limit ?? 5000} 条留存记录中的授权记录，不代表全量历史。最近运行样本上限为 200 条；页面查询于 {lastFetchedAt ? new Date(lastFetchedAt).toLocaleString('zh-CN') : '尚未成功'}；服务端统计时间 {stats?.observed_at ? new Date(stats.observed_at).toLocaleString('zh-CN') : '尚不可用'}。</p>
      </div>

      {!loadFailed && lastFetchedAt && traces.length === 0 ? (
        <section className="rounded-md border border-border-subtle bg-white p-6" aria-live="polite">
          <h3 className="font-medium text-text-primary">当前授权范围内没有运行记录</h3>
          <p className="mt-1 text-sm text-text-secondary">Trace API 成功返回 0 条记录；这与服务错误不同。</p>
          <Link href={runListUrl} className="mt-3 inline-flex min-h-10 items-center text-sm font-medium text-accent underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">打开执行记录</Link>
        </section>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {cards.map((card) => (
              <Link key={card.label} href={statusFilterHref(range, card.status)} className="min-h-24 rounded-md border border-border-subtle bg-white px-4 py-3 hover:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                <span className="block text-sm text-text-secondary">{card.label}</span>
                <strong className={`mt-2 block font-mono text-2xl tabular-nums ${card.tone}`}>{card.value}</strong>
                <span className="mt-1 block text-xs text-text-muted">授权留存记录 · {RUNTIME_WINDOW_LABELS[range]}</span>
              </Link>
            ))}
            <Link href={statusFilterHref(range, 'running')} className="min-h-24 rounded-md border border-violet-200 bg-violet-50/60 px-4 py-3 hover:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" aria-label="运行中（实时）">
              <span className="block text-sm text-violet-800">运行中（实时）</span>
              <strong className="mt-2 block font-mono text-2xl tabular-nums text-violet-900">{activeCount === null ? '未采集' : activeCount}</strong>
              <span className="mt-1 block text-xs text-violet-700">授权活跃端点实时计数 · 点击查看运行中记录</span>
            </Link>
          </div>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            <Link href={statusFilterHref(range, 'success')} className="rounded-md border border-border-subtle bg-white px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
              <span className="block text-xs text-text-secondary">授权记录成功率</span>
              <strong className="mt-1 block font-mono text-xl tabular-nums text-text-primary">{stats ? `${(stats.success_rate * 100).toFixed(1)}%` : '未采集'}</strong>
              <span className="text-xs text-text-muted">{stats?.counts.success ?? 0} 成功 / {stats?.total ?? 0} 条非运行记录</span>
            </Link>
            <div className="rounded-md border border-border-subtle bg-white px-4 py-3">
              <span className="block text-xs text-text-secondary">P50 / P95 耗时</span>
              <strong className="mt-1 block font-mono text-base tabular-nums text-text-primary">{durationLabel(stats?.p50_duration_ms ?? null)} / {durationLabel(stats?.p95_duration_ms ?? null)}</strong>
              <span className="text-xs text-text-muted">授权留存记录中的已知终态，时长大于 0</span>
            </div>
            <div className="rounded-md border border-border-subtle bg-white px-4 py-3">
              <span className="block text-xs text-text-secondary">最近 200 条样本费用（CNY）</span>
              <strong className="mt-1 block font-mono text-base tabular-nums text-text-primary">{sampleSummary.costCny === null ? '未采集' : formatCost(sampleSummary.costCny)}</strong>
              <span className="text-xs text-text-muted">{sampleSummary.costRecordCount}/{sampleSummary.total} 条有 CNY 费用；不换算旧 USD 字段</span>
            </div>
          </div>

          <section className="rounded-md border border-border-subtle bg-white">
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle px-4 py-3">
              <div>
                <h3 className="font-semibold text-text-primary">最近运行</h3>
              <p className="mt-0.5 text-xs text-text-secondary">从最多 200 条授权样本中显示当前时间范围的 8 条；请求与响应内容不在列表展示。</p>
              </div>
              <Link href={runListUrl} className="min-h-10 inline-flex items-center text-sm font-medium text-accent underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">查看执行记录</Link>
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
                    {recent.map((trace) => {
                      const status = getRunStatus(trace)
                      return (
                        <tr key={trace.id} className="hover:bg-bg-elevated">
                          <td className="px-4 py-3"><Link href={detailHref(trace, returnTo)} title={trace.id} className="font-mono text-accent underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">{trace.id.slice(0, 12)}</Link></td>
                          <td className="max-w-64 truncate px-4 py-3 text-text-primary" title={trace.workflow_name || '未记录'}>{trace.workflow_name || '未记录'}</td>
                          <td className="px-4 py-3"><span className={`inline-flex rounded border px-2 py-0.5 text-xs ${getRunStatusTone(status)}`}>{getRunStatusLabel(status)}</span></td>
                          <td className="px-4 py-3 font-mono tabular-nums text-text-secondary">{trace.duration_ms > 0 ? durationLabel(trace.duration_ms) : '未结束'}</td>
                          <td className="px-4 py-3 text-text-secondary" title={new Date(trace.timestamp).toLocaleString('zh-CN')}>{formatRelative(trace.timestamp)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      <section className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
        <h3 className="font-semibold">后端契约缺口</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>列表 DTO 没有执行类型字段；执行类型筛选为 BLOCKED。</li>
          <li>活跃 Trace 查询接口缺少资源级授权；运行中数量及列表为 BLOCKED，不以历史记录推断。</li>
          <li>Run 级待审批状态未随 Trace 返回；不会把运行中当作待审批。</li>
          <li>降级计数只认明确的 <code>metadata.business_outcome=degraded</code>；当前成功记录没有覆盖完整的降级观测率。</li>
          <li>TraceStore 留存上限为 {stats?.retention_limit ?? 5000} 条；统计仅覆盖这批留存记录中的授权数据，最近运行表仍限 200 条样本。</li>
        </ul>
      </section>
    </div>
  )
}
