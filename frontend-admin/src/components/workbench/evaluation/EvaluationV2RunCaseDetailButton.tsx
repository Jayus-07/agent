'use client'

import { useState } from 'react'
import { evaluationService, type EvaluationV2RunCaseDetail } from '@/api/evaluation'

interface Props {
  runId: string
  caseId: string
}

export default function EvaluationV2RunCaseDetailButton({ runId, caseId }: Props) {
  const [detail, setDetail] = useState<EvaluationV2RunCaseDetail | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const show = async () => {
    setOpen(true)
    setLoading(true)
    setError('')
    try {
      setDetail(await evaluationService.getV2RunCaseDetail(runId, caseId))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取逐题详情失败')
      setDetail(null)
    } finally {
      setLoading(false)
    }
  }

  const close = () => setOpen(false)

  return <>
    <button type="button" onClick={() => void show()} className="min-h-9 rounded border border-border-subtle px-3 text-xs text-accent hover:bg-bg-hover" aria-label={`查看用例 ${caseId} 详情`}>
      查看详情
    </button>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onKeyDown={event => { if (event.key === 'Escape') close() }} onMouseDown={event => { if (event.target === event.currentTarget) close() }}>
      <section role="dialog" aria-modal="true" aria-labelledby={`evaluation-case-detail-${caseId}`} className="max-h-[90vh] w-full max-w-5xl overflow-auto rounded-lg border border-border-subtle bg-surface-base p-5 shadow-xl">
        <header className="flex items-start justify-between gap-4 border-b border-border-subtle pb-4">
          <div className="min-w-0"><h3 id={`evaluation-case-detail-${caseId}`} className="font-semibold text-text-primary">逐题详情</h3><p className="mt-1 break-all font-mono text-xs text-text-muted">{caseId}</p></div>
          <button type="button" autoFocus onClick={close} className="min-h-9 shrink-0 rounded border border-border-subtle px-3 text-sm text-text-secondary hover:bg-bg-hover">关闭</button>
        </header>
        {loading && <p className="py-6 text-sm text-text-secondary" role="status">正在读取脱敏后的逐题详情…</p>}
        {error && <p className="mt-4 rounded-md border border-red-200 bg-surface-base p-3 text-sm text-red-800" role="alert">{error}</p>}
        {detail && <div className="mt-4 space-y-4">
          <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-text-secondary">
            <span>用例修订 r{detail.case_revision}</span>
            <span>状态：{detail.result.status}</span>
            <span>阶段耗时：{typeof detail.latency_ms === 'number' ? `${detail.latency_ms} ms` : '—'}</span>
            <span>费用：{detail.cost_status === 'not_reported_by_legacy_runner' ? '旧 Runner 未归因，未按零处理' : `${detail.cost_amount ?? '—'} ${detail.cost_currency ?? ''}`}</span>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <article className="min-w-0 rounded-md bg-bg-elevated p-3"><h4 className="text-sm font-medium text-text-primary">问题期望与证据</h4><pre className="mt-2 max-h-[50vh] overflow-auto whitespace-pre-wrap break-words text-xs text-text-secondary">{JSON.stringify(detail.result.expected, null, 2)}</pre></article>
            <article className="min-w-0 rounded-md bg-bg-elevated p-3"><h4 className="text-sm font-medium text-text-primary">实际回答与检索证据</h4><pre className="mt-2 max-h-[50vh] overflow-auto whitespace-pre-wrap break-words text-xs text-text-secondary">{JSON.stringify(detail.result.actual, null, 2)}</pre></article>
          </div>
          {detail.result.error_msg && <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{detail.result.error_stage || '执行错误'}：{detail.result.error_msg}</p>}
          {detail.trace_id && <p className="text-xs text-text-muted">Trace ID：<code>{detail.trace_id}</code></p>}
        </div>}
      </section>
    </div>}
  </>
}
