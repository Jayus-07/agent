'use client'

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { evaluationService, type EvaluationV2RunCaseDetail } from '@/api/evaluation'
import { ApiError, errMessage } from '@/api/client'
import { StatusBadge, TableWrap, Td, Th, ctl } from './proto/evalUi'

const RAG_QUERY_TYPE_LABELS: Record<string, string> = {
  single_hop_specific: '单跳具体',
  multi_hop_abstract: '多跳抽象',
  multi_hop_specific: '多跳具体',
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function firstValue(source: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    const value = source[key]
    if (value !== undefined && value !== null && value !== '') return value
  }
  return null
}

function textValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value, null, 2) || String(value)
}

function DetailBlock({ title, children }: { title: string; children: ReactNode }) {
  return <section className="rounded-md border border-border-subtle bg-surface-base p-3">
    <h4 className="text-xs font-medium text-text-muted">{title}</h4>
    <div className="mt-1.5 whitespace-pre-wrap break-words text-sm leading-6 text-text-primary">{children}</div>
  </section>
}

function EvidenceList({ items }: { items: Record<string, unknown>[] }) {
  return <div className="space-y-2">
    {items.map((item, index) => {
      const title = firstValue(item, ['title', 'canonical_id', 'source', 'doc_id', 'chunk_id'])
      const snippet = firstValue(item, ['snippet', 'page_content', 'text', 'content'])
      const score = item.rerank_score
      return <article key={`${String(item.doc_id || item.chunk_id || index)}-${index}`} className="rounded-md bg-bg-elevated p-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="font-medium text-text-primary">{title === null ? `证据 ${index + 1}` : textValue(title)}</span>
          <span className="flex items-center gap-2 text-text-muted">
            {item.permission_denied === true && <span className="text-amber-700">权限拒绝</span>}
            {typeof score === 'number' && <span>重排分 {score.toFixed(3)}</span>}
          </span>
        </div>
        {snippet !== null && <p className="mt-1.5 whitespace-pre-wrap break-words text-xs leading-5 text-text-secondary">{textValue(snippet)}</p>}
      </article>
    })}
  </div>
}

function MetricsTable({ title, items }: { title: string; items: Array<[string, unknown]> }) {
  return <section className="space-y-2">
    <h4 className="text-xs font-medium text-text-muted">{title}</h4>
    {items.length === 0
      ? <p className="rounded-md bg-bg-elevated p-2.5 text-xs text-text-muted">本次运行未返回此类评分指标。</p>
      : <TableWrap minWidth={360}>
        <thead className="bg-bg-elevated"><tr><Th>指标</Th><Th end>得分</Th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {items.map(([key, value]) => <tr key={key}><Td>{key}</Td><Td end>{typeof value === 'number' ? value.toFixed(4) : textValue(value)}</Td></tr>)}
        </tbody>
      </TableWrap>}
  </section>
}

function CaseDetailContent({ detail }: { detail: EvaluationV2RunCaseDetail }) {
  const expected = asRecord(detail.result.expected)
  const actual = asRecord(detail.result.actual)
  const pipeline = asRecord(actual.pipeline)
  const query = firstValue(expected, ['query', 'question']) ?? firstValue(actual, ['query', 'question'])
  const queryType = firstValue(pipeline, ['query_type']) ?? firstValue(actual, ['query_type'])
  const reference = firstValue(expected, ['reference_answer', 'expected_answer', 'ground_truth', 'answer', 'expected_behavior'])
  const requiredFacts = firstValue(expected, ['required_facts', 'facts'])
  const expectedEvidence = firstValue(expected, ['evidence', 'ground_truth_context', 'citations', 'evidence_refs'])
  const retrievalDetails = Array.isArray(actual.details)
    ? actual.details.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : []
  const retrievedEvidence = firstValue(actual, ['retrieved_contexts', 'contexts', 'retrieved_docs', 'actual_doc_strs'])
    ?? firstValue(asRecord(actual.ragas_input), ['contexts'])
  const answer = firstValue(actual, ['generated_answer', 'final_answer', 'answer', 'output', 'response'])
  const explanation = firstValue(actual, ['reason', 'rationale', 'judge_reason'])
  const metrics = Object.entries(detail.result.metrics || {})
  const retrievalMetrics = metrics.filter(([key]) => {
    const normalized = key.toLowerCase()
    return /^(recall@|precision@|context_noise|mrr$|ndcg@|chunk_|top1_accuracy$|reject_top1_accuracy$|dept_leak$|multi_hop_success$|sem_context_|retrieval)/.test(normalized)
  })
  const answerMetrics = metrics.filter(([key]) => {
    const normalized = key.toLowerCase()
    return /^(gen_|sem_(answer|faithfulness)|required_fact|false_answer|citation_|reject_accuracy$|judge_|ragas|answer_)/.test(normalized)
      || /answer|faithfulness|correctness|judge|citation/.test(normalized)
  })
  const otherMetrics = metrics.filter(([key]) => !retrievalMetrics.some(([candidate]) => candidate === key) && !answerMetrics.some(([candidate]) => candidate === key))
  const stageMetrics = asRecord(actual.stage_metrics)
  const retrievalStages = Object.entries(stageMetrics).filter(([key]) => /^S[1-5]_/i.test(key))
  const answerStages = Object.entries(stageMetrics).filter(([key]) => /^(generation|S[6-7]_)/i.test(key))

  return <div className="space-y-3 p-4">
    <div className="flex flex-wrap gap-x-5 gap-y-2 rounded-md bg-bg-elevated p-3 text-xs text-text-secondary">
      <span>用例修订：r{detail.case_revision}</span>
      <span>状态：<StatusBadge status={detail.result.status} /></span>
      {queryType !== null && <span>RAG 题型：{RAG_QUERY_TYPE_LABELS[textValue(queryType)] || textValue(queryType)}</span>}
      <span>时延：{typeof detail.latency_ms === 'number' ? `${detail.latency_ms} ms` : '—'}</span>
      <span>费用：{detail.cost_status === 'not_reported_by_legacy_runner' ? '旧 Runner 未归因' : `${detail.cost_amount ?? '—'} ${detail.cost_currency ?? ''}`.trim()}</span>
    </div>

    <DetailBlock title="用户问题">{textValue(query)}</DetailBlock>
    <DetailBlock title="标准答案 / 预期行为">
      {reference !== null ? textValue(reference) : textValue(expected)}
      {requiredFacts !== null && <p className="mt-2 text-xs text-text-secondary">必需事实：{textValue(requiredFacts)}</p>}
    </DetailBlock>
    <DetailBlock title="模型回答">{answer !== null ? textValue(answer) : textValue(actual)}</DetailBlock>
    {retrievalDetails.length > 0
      ? <DetailBlock title="检索结果与命中证据"> <EvidenceList items={retrievalDetails} /> </DetailBlock>
      : retrievedEvidence !== null && <DetailBlock title="检索结果与命中证据">{textValue(retrievedEvidence)}</DetailBlock>}
    {expectedEvidence !== null && <DetailBlock title="标准证据">{textValue(expectedEvidence)}</DetailBlock>}
    <MetricsTable title="检索质量评分" items={retrievalMetrics} />
    {retrievalStages.length > 0 && <DetailBlock title="检索阶段记录">{textValue(Object.fromEntries(retrievalStages))}</DetailBlock>}
    <MetricsTable title="答案质量评分" items={answerMetrics} />
    {answerStages.length > 0 && <DetailBlock title="答案生成阶段记录">{textValue(Object.fromEntries(answerStages))}</DetailBlock>}
    {otherMetrics.length > 0 && <MetricsTable title="其他评分指标" items={otherMetrics} />}
    {explanation !== null && <DetailBlock title="评分理由">{textValue(explanation)}</DetailBlock>}
    {detail.result.error_msg && <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">{detail.result.error_stage || '执行错误'}：{detail.result.error_msg}</p>}

    {detail.trace_id && <p className="break-all text-xs text-text-muted">Trace：
      <a className="ml-1 text-accent hover:underline" href={`/observability/monitoring?tab=traces&q=${encodeURIComponent(detail.trace_id)}`}>{detail.trace_id}</a>
    </p>}
  </div>
}

export default function EvaluationV2RunCaseDetailButton({ runId, caseId }: { runId: string; caseId: string }) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const titleId = `evaluation-case-detail-${runId}-${caseId}`
  const detailQuery = useQuery({
    queryKey: ['admin', 'evaluation-v2-run-case-detail', runId, caseId],
    queryFn: () => evaluationService.getV2RunCaseDetail(runId, caseId),
    enabled: open,
    staleTime: 30_000,
  })

  useEffect(() => {
    if (!open) return
    const trigger = triggerRef.current
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setOpen(false)
        return
      }
      if (event.key !== 'Tab') return
      const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) || [])
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      trigger?.focus()
    }
  }, [open])

  return <>
    <button ref={triggerRef} type="button" onClick={() => setOpen(true)} className={ctl} aria-label={`查看用例 ${caseId} 详情`}>
      查看
    </button>
    <div
      className={`fixed inset-0 z-50 ${open ? 'visible' : 'invisible'}`}
      aria-hidden={!open}
      onMouseDown={event => { if (event.target === event.currentTarget) setOpen(false) }}
    >
      <div className={`absolute inset-0 bg-black/40 transition-opacity duration-200 motion-reduce:transition-none ${open ? 'opacity-100' : 'opacity-0'}`} onMouseDown={() => setOpen(false)} />
      <section
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`absolute inset-y-0 right-0 flex h-full w-full max-w-[42rem] flex-col border-l border-border-subtle bg-surface-base shadow-2xl transition-transform duration-200 ease-out motion-reduce:transition-none ${open ? 'translate-x-0' : 'translate-x-full'}`}
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-border-subtle bg-surface-base px-5 py-4">
          <div className="min-w-0">
            <h3 id={titleId} className="text-sm font-semibold text-text-primary">逐题详情</h3>
            <p className="mt-1 break-all font-mono text-[11px] text-text-muted">Run {runId.slice(0, 8)} · Case {caseId}</p>
          </div>
          <button ref={closeRef} type="button" onClick={() => setOpen(false)} className={ctl} aria-label="关闭用例详情">关闭</button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {detailQuery.isPending && <p role="status" className="p-5 text-sm text-text-secondary">正在读取脱敏后的逐题详情…</p>}
          {detailQuery.isError && <div className="space-y-3 p-5">
            <p role="alert" className="rounded-md border border-red-200 p-3 text-sm text-red-800">{errMessage(detailQuery.error)}</p>
            {!(detailQuery.error instanceof ApiError && detailQuery.error.status === 404) &&
              <button type="button" className={ctl} onClick={() => void detailQuery.refetch()}>重新读取</button>}
          </div>}
          {detailQuery.data && <CaseDetailContent detail={detailQuery.data} />}
        </div>
      </section>
    </div>
  </>
}
