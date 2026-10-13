'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { listTraces } from '@/api/observability'
import type { TraceRecord } from '@/types/trace'
import {
  getRunStatus,
  getRunStatusLabel,
  getRunStatusTone,
  isInRuntimeWindow,
  RUNTIME_WINDOW_LABELS,
  summarizeRuntimeSample,
  type RuntimeWindow,
} from '@/lib/observability/runtimeStatus'
import { formatCost, formatRelative } from '@/types/trace'

const WINDOWS: RuntimeWindow[] = ['15m', '1h', '6h', '24h']
const WINDOW_VALUES = new Set<string>(WINDOWS)

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
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const hasFetched = useRef(false)

  const loadTraces = useCallback(async () => {
    if (hasFetched.current) setRefreshing(true)
    else setLoading(true)
    try {
      // 只使用已由 Trace API 做资源授权的真实记录；不调用未带读取授权的聚合 stats 接口。
      const result = await listTraces(200)
      setTraces(result)
      hasFetched.current = true
      setLastFetchedAt(new Date().toISOString())
      setLoadFailed(false)
    } catch {
      setLoadFailed(true)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void loadTraces()
  }, [loadTraces])

  const inWindow = useMemo(
    () => traces.filter((trace) => isInRuntimeWindow(trace, range)),
    [range, traces],
  )
  const summary = useMemo(
    () => summarizeRuntimeSample(traces, range),
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
      .filter((trace) => getRunStatus(trace) !== 'running')
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
      .slice(0, 8),
    [inWindow],
  )

  const cards = [
    { label: '记录数', value: String(summary.total), status: 'all', tone: 'text-slate-900' },
    { label: '成功', value: String(summary.counts.success), status: 'success', tone: 'text-emerald-700' },
    { label: '业务失败', value: String(summary.counts.failed), status: 'failed', tone: 'text-red-700' },
    { label: '执行错误', value: String(summary.counts.error), status: 'error', tone: 'text-red-700' },
    { label: '降级', value: String(summary.counts.degraded), status: 'degraded', tone: 'text-amber-700' },
    { label: '部分成功', value: String(summary.counts.partial), status: 'partial', tone: 'text-amber-700' },
    { label: '超时', value: String(summary.counts.timeout), status: 'timeout', tone: 'text-amber-700' },
    { label: '已拒绝', value: String(summary.counts.rejected), status: 'rejected', tone: 'text-orange-700' },
    { label: '已取消', value: String(summary.counts.cancelled), status: 'cancelled', tone: 'text-slate-700' },
    { label: '状态未知', value: String(summary.counts.unknown), status: 'unknown', tone: 'text-slate-600' },
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
            指标仅统计当前授权范围内最近返回的记录样本；成功率 = 成功终态数 ÷ 已知终态数。
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
        <p>数据源：<code>GET /api/observability/traces?limit=200</code> · 服务端执行身份与资源级授权 · 当前时间范围：{RUNTIME_WINDOW_LABELS[range]}</p>
        <p>本次查询于 {lastFetchedAt ? new Date(lastFetchedAt).toLocaleString('zh-CN') : '尚未成功'}；接口未返回 <code>observed_at</code>，源数据新鲜度无法核验。统计上限为最近 200 条，未声称覆盖全量运行。</p>
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
                <span className="mt-1 block text-xs text-text-muted">样本 · {RUNTIME_WINDOW_LABELS[range]}</span>
              </Link>
            ))}
            <div className="min-h-24 rounded-md border border-amber-200 bg-amber-50 px-4 py-3" aria-label="运行中状态暂不可统计">
              <span className="block text-sm text-amber-900">运行中</span>
              <strong className="mt-2 block text-base font-semibold text-amber-900">暂不可统计</strong>
              <span className="mt-1 block text-xs text-amber-800">BLOCKED · 活跃 Trace 缺少资源授权</span>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            <Link href={statusFilterHref(range, 'success')} className="rounded-md border border-border-subtle bg-white px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
              <span className="block text-xs text-text-secondary">样本成功率</span>
              <strong className="mt-1 block font-mono text-xl tabular-nums text-text-primary">{summary.successRate === null ? '未采集' : `${(summary.successRate * 100).toFixed(1)}%`}</strong>
              <span className="text-xs text-text-muted">{summary.counts.success} 成功 / {summary.terminalCount} 个已知终态</span>
            </Link>
            <div className="rounded-md border border-border-subtle bg-white px-4 py-3">
              <span className="block text-xs text-text-secondary">P50 / P95 耗时</span>
              <strong className="mt-1 block font-mono text-base tabular-nums text-text-primary">{durationLabel(summary.p50DurationMs)} / {durationLabel(summary.p95DurationMs)}</strong>
              <span className="text-xs text-text-muted">仅计时长大于 0 的已终态样本</span>
            </div>
            <div className="rounded-md border border-border-subtle bg-white px-4 py-3">
              <span className="block text-xs text-text-secondary">已采集费用（CNY）</span>
              <strong className="mt-1 block font-mono text-base tabular-nums text-text-primary">{summary.costCny === null ? '未采集' : formatCost(summary.costCny)}</strong>
              <span className="text-xs text-text-muted">{summary.costRecordCount}/{summary.total} 条有 CNY 费用；不换算旧 USD 字段</span>
            </div>
          </div>

          <section className="rounded-md border border-border-subtle bg-white">
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle px-4 py-3">
              <div>
                <h3 className="font-semibold text-text-primary">最近运行</h3>
                <p className="mt-0.5 text-xs text-text-secondary">当前时间范围内最多显示 8 条；请求与响应内容不在列表展示。</p>
              </div>
              <Link href={runListUrl} className="min-h-10 inline-flex items-center text-sm font-medium text-accent underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">查看执行记录</Link>
            </div>
            {recent.length === 0 ? (
              <p className="px-4 py-6 text-sm text-text-secondary">最近 200 条授权记录中，所选时间范围没有可确认的终态记录；历史列表的 running 状态不会当作实时运行。</p>
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
          <li>列表没有 <code>observed_at</code>、总数或游标，当前指标是最多 200 条授权记录的样本。</li>
        </ul>
      </section>
    </div>
  )
}
