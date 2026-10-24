'use client'

import { useState } from 'react'
import { evaluationService, type EvaluationV2RunCaseDetail } from '@/api/evaluation'

interface Props {
  baselineRunId: string
  candidateRunId: string
  caseId: string
}

function costText(detail: EvaluationV2RunCaseDetail): string {
  if (detail.cost_status === 'not_reported_by_legacy_runner') return '旧 Runner 未归因'
  return `${detail.cost_amount ?? '—'} ${detail.cost_currency ?? ''}`.trim()
}

export default function EvaluationV2ComparisonCaseDetailButton({ baselineRunId, candidateRunId, caseId }: Props) {
  const [details, setDetails] = useState<{ baseline: EvaluationV2RunCaseDetail; candidate: EvaluationV2RunCaseDetail } | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const show = async () => {
    setOpen(true)
    setLoading(true)
    setError('')
    try {
      const [baseline, candidate] = await Promise.all([
        evaluationService.getV2RunCaseDetail(baselineRunId, caseId),
        evaluationService.getV2RunCaseDetail(candidateRunId, caseId),
      ])
      setDetails({ baseline, candidate })
    } catch (cause) {
      setDetails(null)
      setError(cause instanceof Error ? cause.message : '读取 A/B 逐题差异失败')
    } finally {
      setLoading(false)
    }
  }

  return <>
    <button type="button" onClick={() => void show()} className="min-h-9 rounded border border-border-subtle px-3 text-xs text-accent hover:bg-bg-hover" aria-label={`查看用例 ${caseId} 的 A/B 回答差异`}>
      查看回答差异
    </button>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onKeyDown={event => { if (event.key === 'Escape') setOpen(false) }} onMouseDown={event => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section role="dialog" aria-modal="true" aria-labelledby={`evaluation-compare-detail-${caseId}`} className="max-h-[90vh] w-full max-w-6xl overflow-auto rounded-lg border border-border-subtle bg-surface-base p-5 shadow-xl">
        <header className="flex items-start justify-between gap-4 border-b border-border-subtle pb-4">
          <div className="min-w-0"><h3 id={`evaluation-compare-detail-${caseId}`} className="font-semibold text-text-primary">A/B 逐题回答差异</h3><p className="mt-1 break-all font-mono text-xs text-text-muted">{caseId}</p></div>
          <button type="button" autoFocus onClick={() => setOpen(false)} className="min-h-9 shrink-0 rounded border border-border-subtle px-3 text-sm text-text-secondary hover:bg-bg-hover">关闭</button>
        </header>
        {loading && <p role="status" className="py-6 text-sm text-text-secondary">正在读取服务端脱敏结果…</p>}
        {error && <p role="alert" className="mt-4 rounded-md border border-red-200 p-3 text-sm text-red-800">{error}</p>}
        {details && <div className="mt-4 space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            {([['基线 A', details.baseline], ['候选 B', details.candidate]] as const).map(([label, detail]) => <article key={label} className="rounded-md border border-border-subtle p-3">
              <h4 className="text-sm font-semibold text-text-primary">{label}</h4>
              <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-text-secondary"><dt>状态</dt><dd>{detail.result.status}</dd><dt>耗时</dt><dd>{typeof detail.latency_ms === 'number' ? `${detail.latency_ms} ms` : '—'}</dd><dt>费用</dt><dd>{costText(detail)}</dd></dl>
              <p className="mt-2 text-xs font-medium text-text-primary">实际回答与检索证据</p>
              <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-bg-elevated p-2 text-xs text-text-secondary">{JSON.stringify(detail.result.actual, null, 2)}</pre>
              <p className="mt-2 text-xs font-medium text-text-primary">评分</p>
              <pre className="mt-1 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded bg-bg-elevated p-2 text-xs text-text-secondary">{JSON.stringify(detail.result.metrics, null, 2)}</pre>
              {detail.result.error_msg && <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">{detail.result.error_stage || '执行错误'}：{detail.result.error_msg}</p>}
              {detail.trace_id && <p className="mt-2 break-all text-[11px] text-text-muted">Trace ID：{detail.trace_id}</p>}
            </article>)}
          </div>
          <article className="rounded-md bg-bg-elevated p-3"><h4 className="text-sm font-medium text-text-primary">共同问题期望与证据</h4><pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-words text-xs text-text-secondary">{JSON.stringify(details.baseline.result.expected, null, 2)}</pre></article>
        </div>}
      </section>
    </div>}
  </>
}
