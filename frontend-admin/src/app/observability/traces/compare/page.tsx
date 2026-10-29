'use client'

/**
 * /observability/traces/compare — Trace 并排对比（2–4 条）
 *
 * 入口在执行记录列表（TraceListPanel 选中后「对比 N 条」）。此前该路由缺失，
 * 选中后跳转会误入 [id] 详情页。此处只读对比核心指标（状态/耗时/Token/
 * 费用/模型/错误摘要），完整视图各自下钻详情页；不在对比页重复实现详情。
 * 权限沿用 /observability 布局段 RoleGate（admin 及以上）。
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { getTraceDetail } from '@/api/observability'
import TraceBreadcrumb from '@/components/observability/trace/TraceBreadcrumb'
import {
  getRunStatus,
  getRunStatusLabel,
  getRunStatusTone,
} from '@/lib/observability/runtimeStatus'
import type { TraceRecord } from '@/types/trace'

const MAX_COMPARE = 4

function fmtMs(ms?: number): string {
  if (ms == null) return '—'
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`
  return `${Math.round(ms)} ms`
}

function fmtNum(n?: number): string {
  return typeof n === 'number' ? n.toLocaleString('zh-CN') : '—'
}

function fmtCny(cny?: number): string {
  return typeof cny === 'number' ? `¥${cny.toFixed(4)}` : '—'
}

function errorSummary(trace: TraceRecord): string {
  const err = trace.error as Record<string, unknown>
  const msg = typeof err?.message === 'string' ? err.message : ''
  return msg ? msg.slice(0, 120) : '—'
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

  if (ids.length < 2) {
    return (
      <div className="p-6">
        <TraceBreadcrumb crumbs={[{ label: '运行控制中心', href: '/observability/monitoring?tab=overview' }, { label: 'Trace 对比' }]} />
        <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900" role="status">
          对比至少需要 2 条 Trace。请回到执行记录列表勾选后发起对比。
        </div>
        <Link href="/observability/monitoring?tab=traces" className="mt-3 inline-flex min-h-10 items-center rounded-md border border-border-subtle bg-white px-3 text-sm text-text-secondary hover:bg-bg-hover">
          返回执行记录
        </Link>
      </div>
    )
  }

  const rows: { label: string; render: (t: TraceRecord) => React.ReactNode }[] = [
    {
      label: '状态',
      render: (t) => {
        const status = getRunStatus(t)
        return <span className={getRunStatusTone(status)}>{getRunStatusLabel(status)}</span>
      },
    },
    { label: '耗时', render: (t) => <span className="font-mono">{fmtMs(t.duration_ms)}</span> },
    {
      label: 'Token（总）',
      render: (t) => <span className="font-mono">{fmtNum(t.usage?.total_tokens)}</span>,
    },
    { label: '费用', render: (t) => <span className="font-mono">{fmtCny(t.cost_cny)}</span> },
    { label: '模型', render: (t) => <span className="font-mono">{t.model?.name || '—'}</span> },
    { label: '工作流', render: (t) => <span className="font-mono">{t.workflow_name || '—'}</span> },
    {
      label: '开始时间',
      render: (t) => <span className="font-mono">{t.timestamp.replace('T', ' ').slice(0, 19)}</span>,
    },
    { label: '错误摘要', render: (t) => <span title={errorSummary(t)}>{errorSummary(t)}</span> },
  ]

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <TraceBreadcrumb crumbs={[{ label: '运行控制中心', href: '/observability/monitoring?tab=overview' }, { label: '执行记录', href: '/observability/monitoring?tab=traces' }, { label: 'Trace 对比' }]} />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Trace 对比（{ids.length} 条）</h2>
          <p className="mt-1 text-sm text-text-secondary">只读对比核心指标；完整输入输出与 Span 视图请下钻各条详情。</p>
        </div>
        <Link href={returnTo} className="inline-flex min-h-10 items-center rounded-md border border-border-subtle bg-white px-3 text-sm text-text-secondary hover:bg-bg-hover">
          返回列表
        </Link>
      </div>

      {failed && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          对比数据读取失败。没有用空表格或模拟数据替代。
        </div>
      )}

      {traces === null && !failed && (
        <div className="p-6 text-sm text-text-secondary" role="status">正在读取对比 Trace…</div>
      )}

      {traces !== null && (
        <div className="overflow-x-auto rounded-md border border-border-subtle bg-white">
          <table className="w-full text-left text-[12px]">
            <thead>
              <tr className="border-b border-border-subtle text-[11px] text-text-muted">
                <th className="px-4 py-2 font-normal">指标</th>
                {traces.map((t, i) => (
                  <th key={ids[i]} className="px-4 py-2 font-normal">
                    {t ? (
                      <Link href={`/observability/traces/${t.id}`} className="font-mono text-accent hover:underline">
                        {t.id.slice(0, 12)}…
                      </Link>
                    ) : (
                      <span className="font-mono text-text-muted">{ids[i].slice(0, 12)}…</span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label} className="border-t border-border-subtle">
                  <td className="px-4 py-2 text-text-muted">{row.label}</td>
                  {traces.map((t, i) => (
                    <td key={ids[i]} className="px-4 py-2 text-text-secondary">
                      {t ? row.render(t) : <span className="text-amber-700">读取失败/不存在</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
