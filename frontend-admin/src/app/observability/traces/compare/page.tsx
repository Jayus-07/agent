'use client'

/**
 * /observability/traces/compare — Trace 并排对比（2–4 条）
 *
 * 入口在执行记录列表（TraceListPanel 选中后「对比 N 条」），参数契约 `?ids=`。
 * 2026-10-31 按 Trace 详情页「四问」视觉重构（旧版为单表指标矩阵）：
 *   - 每条 Trace 一栏卡片：运行对象徽标（runKind）/ 状态 / 耗时 / Token /
 *     费用 / 模型 / 错误摘要 + SpanGantt 双实例；
 *   - 恰有两条可读时对耗时/Token/费用做差异高亮（更高=amber、更低=emerald）；
 *   - 空值如实显示「未采集」，不用 0 或 — 伪造；完整视图下钻详情页。
 * 权限沿用 /observability 布局段 RoleGate（admin 及以上）。
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { getTraceDetail } from '@/api/observability'
import TraceBreadcrumb from '@/components/observability/trace/TraceBreadcrumb'
import SpanGantt from '@/components/observability/trace/SpanGantt'
import { formatCost } from '@/types/trace'
import type { Span, TraceRecord } from '@/types/trace'
import {
  getRunStatus,
  getRunStatusLabel,
  getRunStatusTone,
} from '@/lib/observability/runtimeStatus'
import { deriveRunKind, runKindBadgeClass } from '@/lib/observability/runKind'

const MAX_COMPARE = 4

interface DiffHint {
  text: string
  cls: string
}

function fmtMs(ms?: number): string | null {
  if (ms == null || ms <= 0) return null
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`
}

/** 恰有两条且双方数值齐备时给出差值提示；更高（更慢/更贵/更多）为 amber，更低为 emerald。 */
function diffHint(value: number | undefined, other: number | undefined, unit: string, fmt: (n: number) => string): DiffHint | null {
  if (typeof value !== 'number' || typeof other !== 'number' || value === other) return null
  const delta = value - other
  return {
    text: `${unit} ${delta > 0 ? '+' : '−'}${fmt(Math.abs(delta))}`,
    cls: delta > 0 ? 'text-amber-600' : 'text-emerald-600',
  }
}

function fmtDeltaMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`
}

function fmtDeltaTokens(n: number): string {
  return Math.round(n).toLocaleString('zh-CN')
}

function errorSummary(trace: TraceRecord): string {
  const err = trace.error as Record<string, unknown>
  const code = typeof err?.code === 'string' && err.code.trim() ? `${err.code.trim()} · ` : ''
  const msg = typeof err?.message === 'string' ? err.message : ''
  return `${code}${msg}`.slice(0, 120)
}

function MetricRow({ label, value, hint }: { label: string; value: React.ReactNode; hint?: DiffHint | null }) {
  return (
    <div className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span className="flex items-baseline gap-2 font-mono text-xs">
        {value}
        {hint && <span className={`font-sans text-[10px] font-medium ${hint.cls}`}>{hint.text}</span>}
      </span>
    </div>
  )
}

export default function TraceComparePage() {
  const searchParams = useSearchParams()
  const [traces, setTraces] = useState<(TraceRecord | null)[] | null>(null)
  const [failed, setFailed] = useState(false)

  const ids = Array.from(new Set((searchParams.get('ids') || '').split(',').filter(Boolean))).slice(0, MAX_COMPARE)
  const returnTo = searchParams.get('returnTo') || '/observability/monitoring?tab=traces'

  useEffect(() => {
    let cancelled = false
    setTraces(null)
    setFailed(false)
    if (ids.length < 2) return
    Promise.all(ids.map((id) => getTraceDetail(id).catch(() => null)))
      .then((results) => {
        if (!cancelled) setTraces(results)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
    // ids 来自 URL，序列化后作依赖避免数组身份重建
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids.join(',')])

  const loaded = traces?.filter((t): t is TraceRecord => t !== null) ?? []
  const pairDiff = loaded.length === 2 && ids.length === 2

  const columns = traces === null ? ids.map((id) => ({ id, trace: null as TraceRecord | null })) : ids.map((id, i) => ({ id, trace: traces[i] ?? null }))

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-[1440px] space-y-5 px-4 py-5 sm:px-6 sm:py-6">
        <TraceBreadcrumb crumbs={[{ label: '运行控制中心', href: '/observability/monitoring?tab=overview' }, { label: '执行记录', href: '/observability/monitoring?tab=traces' }, { label: 'Trace 对比' }]} />

        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Trace 对比（{ids.length} 条）</h1>
            <p className="mt-1 text-sm text-slate-500">并排对比核心指标与 Span 时间线；完整输入输出与 LLM 调用请下钻各条详情页。</p>
          </div>
          <Link href={returnTo} className="inline-flex min-h-10 items-center rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-600 hover:bg-slate-100">
            返回列表
          </Link>
        </div>

        {ids.length < 2 && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900" role="status">
            对比至少需要 2 条 Trace。请回到执行记录列表勾选后发起对比。
          </div>
        )}

        {failed && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
            对比数据读取失败。没有用空表格或模拟数据替代。
          </div>
        )}

        {traces === null && !failed && ids.length >= 2 && (
          <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500" role="status">正在读取对比 Trace…</div>
        )}

        {columns.length >= 2 && (
          <div className="grid gap-5 md:grid-cols-2">
            {columns.map(({ id, trace }) => {
              if (!trace) {
                return (
                  <section key={id} className="rounded-xl border border-dashed border-slate-300 bg-white p-5">
                    <span className="font-mono text-sm text-slate-400">{id.slice(0, 12)}…</span>
                    <p className="mt-3 text-sm text-amber-700">读取失败/不存在</p>
                  </section>
                )
              }
              const spans = (trace.spans || []) as Span[]
              const rootSpan = spans.find((s) => s.parent_id === null)
              const uncoveredMs = rootSpan ? Number(rootSpan.metrics?.uncovered_ms ?? 0) : 0
              const runStatus = getRunStatus(trace)
              const kind = deriveRunKind(trace)
              const other = loaded.find((t) => t.id !== trace.id)
              const durationText = fmtMs(trace.duration_ms)
              const totalTokens = typeof trace.usage?.total_tokens === 'number' ? trace.usage.total_tokens.toLocaleString('zh-CN') : null
              const costText = typeof trace.cost_cny === 'number' ? formatCost(trace.cost_cny) : null
              const errText = errorSummary(trace)
              return (
                <section key={id} className="rounded-xl border border-slate-200 bg-white p-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link href={`/observability/traces/${trace.id}`} className="font-mono text-sm font-semibold text-blue-700 hover:underline">
                      {trace.id.slice(0, 12)}…
                    </Link>
                    <span className={`inline-flex rounded border px-2 py-0.5 text-xs font-medium ${runKindBadgeClass(kind.key)}`}>{kind.label}</span>
                    <span className={`inline-flex rounded border px-2 py-0.5 text-xs font-semibold ${getRunStatusTone(runStatus)}`}>{getRunStatusLabel(runStatus)}</span>
                    <span className="ml-auto text-[10px] text-slate-400">{trace.timestamp.replace('T', ' ').slice(0, 19)}</span>
                  </div>

                  <div className="mt-4 space-y-1.5 text-sm">
                    <MetricRow label="总耗时" value={durationText ?? <span className="text-slate-400">未采集</span>}
                      hint={pairDiff && durationText ? diffHint(trace.duration_ms, other?.duration_ms, '耗时', fmtDeltaMs) : null} />
                    <MetricRow label="Token（总）" value={totalTokens ?? <span className="text-slate-400">未采集</span>}
                      hint={pairDiff ? diffHint(trace.usage?.total_tokens, other?.usage?.total_tokens, 'Token', fmtDeltaTokens) : null} />
                    <MetricRow label="费用（CNY）" value={costText ?? <span className="text-slate-400">未采集</span>}
                      hint={pairDiff ? diffHint(trace.cost_cny, other?.cost_cny, '费用', formatCost) : null} />
                    <MetricRow label="模型（main）" value={trace.model?.name
                      ? <span className="text-xs">{trace.model.name}</span>
                      : <span className="text-slate-400">未采集</span>} />
                    <MetricRow label="工作流" value={trace.workflow_name
                      ? <span className="text-xs">{trace.workflow_name}</span>
                      : <span className="text-slate-400">未记录</span>} />
                  </div>

                  {errText && (
                    <div className="mt-3 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700" role="note">
                      {errText}
                    </div>
                  )}

                  <div className="mt-4">
                    <p className="mb-2 text-[10px] uppercase tracking-widest text-slate-400">Span 甘特时间线</p>
                    {spans.length > 0 ? (
                      <SpanGantt spans={spans} totalMs={trace.duration_ms} uncoveredMs={uncoveredMs} />
                    ) : (
                      <p className="py-4 text-center text-xs text-slate-400">本 Trace 无 Span 记录（失败过早或采集链未覆盖）。</p>
                    )}
                  </div>
                </section>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
