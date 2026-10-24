'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { createIdempotencyKey } from '@/api/client'
import { evaluationService, type EvaluationV2Candidate, type EvaluationV2EvidenceClaim } from '@/api/evaluation'

const control = 'min-h-10 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm text-text-primary disabled:opacity-50'
const emptyExpected = { behavior: 'answer', reference_answer: '', required_facts: [], evidence: [] }

function initialExpected(candidate: EvaluationV2Candidate): Record<string, unknown> {
  const proposed = candidate.payload_json.proposed_facts?.[0]
  if (!proposed || typeof proposed !== 'object') return emptyExpected
  return { ...proposed, evidence: [] }
}

function initialEvidence(candidate: EvaluationV2Candidate): EvaluationV2EvidenceClaim[] {
  return (candidate.payload_json.proposed_evidence || []).map(item => ({
    kb_id: String(item.kb_id || ''),
    doc_id: String(item.doc_id || ''),
    doc_version: String(item.doc_version || ''),
    quote: String(item.quote || ''),
  }))
}

export default function EvaluationV2ReviewPanel() {
  const [items, setItems] = useState<EvaluationV2Candidate[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [expectedText, setExpectedText] = useState(JSON.stringify(emptyExpected, null, 2))
  const [evidenceText, setEvidenceText] = useState('[]')
  const [rationale, setRationale] = useState('')
  const [traceId, setTraceId] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const selected = items.find(item => item.candidate_id === selectedId)
  const selectedDomain = String(selected?.payload_json.source_ref?.module || 'rag')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await evaluationService.listV2Candidates('in_review')
      setItems(result)
      setSelectedId(current => result.some(item => item.candidate_id === current) ? current : result[0]?.candidate_id || '')
      setMessage('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取 V2 审核队列失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!selected) return
    setExpectedText(JSON.stringify(initialExpected(selected), null, 2))
    setEvidenceText(JSON.stringify(initialEvidence(selected), null, 2))
    setRationale('')
  }, [selected?.candidate_id, selected?.revision])

  const review = async (verdict: 'approved' | 'rejected') => {
    if (!selected) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const expected = verdict === 'approved' ? JSON.parse(expectedText) as Record<string, unknown> : undefined
      const evidence = verdict === 'approved' ? JSON.parse(evidenceText) as EvaluationV2EvidenceClaim[] : undefined
      await evaluationService.reviewV2Candidate(selected.candidate_id, {
        expected_revision: selected.revision,
        verdict,
        rationale: rationale.trim(),
        expected,
        evidence,
      })
      setMessage(verdict === 'approved' ? '审核通过，样本已进入 Golden 候选池。' : '候选已驳回并保留审计记录。')
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存 V2 审核失败')
    } finally {
      setBusy(false)
    }
  }

  const intakeTrace = async () => {
    if (!traceId.trim()) return
    setBusy(true); setError(''); setMessage('')
    try {
      const created = await evaluationService.intakeV2TraceCandidate(traceId.trim(), createIdempotencyKey())
      setTraceId('')
      setMessage(`Trace 候选 ${created.candidate_id} 已进入审核队列；原始答案只作 observed_answer 留存。`)
      await load()
      setSelectedId(created.candidate_id)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '从 Trace 创建 V2 候选失败') }
    finally { setBusy(false) }
  }

  return <section className="rounded-lg border border-border-subtle bg-surface-base p-4" aria-labelledby="evaluation-v2-review-heading">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 id="evaluation-v2-review-heading" className="text-base font-semibold text-text-primary">V2 候选审核</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">审核人确认模块标准结果。RAG 回答样本必须提供 KB、文档版本与原文引用，服务端按当前可信权限重新核验；其他模块使用各自的离线字段契约，不接收知识库证据。</p></div><button type="button" onClick={() => void load()} disabled={loading} className={`${control} inline-flex items-center gap-2`}><RefreshCw size={15} />刷新队列</button></div>
    {error && <p role="alert" className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
    {message && <p role="status" className="mt-3 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-900">{message}</p>}
    <div className="mt-4 flex flex-wrap items-end gap-2 rounded-md bg-bg-elevated p-3"><label className="min-w-56 flex-1 text-xs text-text-secondary">线上 Trace ID<input value={traceId} onChange={event => setTraceId(event.target.value)} className={`${control} mt-1 w-full`} placeholder="粘贴当前身份可见的 Trace ID" /></label><button type="button" onClick={() => void intakeTrace()} disabled={busy || !traceId.trim()} className={`${control} bg-accent text-white`}>从 Trace 创建候选</button><span className="w-full text-xs text-text-muted">服务端会再次检查 Trace 可见性；请求体不能指定租户或审核身份。</span></div>
    {loading && items.length === 0 ? <p role="status" className="mt-4 text-sm text-text-secondary">正在读取持久化候选…</p> : items.length === 0 ? <p className="mt-4 rounded-md bg-bg-elevated p-4 text-sm text-text-secondary">V2 审核队列为空。请在数据集中心预检并导入候选。</p> : <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(240px,0.75fr)_minmax(0,1.4fr)]">
      <div className="max-h-[32rem] space-y-2 overflow-auto" aria-label="待审核 V2 候选">{items.map(item => <button key={item.candidate_id} type="button" aria-pressed={selectedId === item.candidate_id} onClick={() => setSelectedId(item.candidate_id)} className={'w-full rounded-md border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ' + (selectedId === item.candidate_id ? 'border-accent bg-accent-soft' : 'border-border-subtle hover:bg-bg-hover')}><span className="block line-clamp-2 text-sm font-medium text-text-primary">{item.payload_json.input?.query || '无问题文本'}</span><span className="mt-1 block text-xs text-text-muted">{String(item.payload_json.source_ref?.dataset_key || item.payload_json.source_ref?.trace_id || item.candidate_id)} · 修订 {item.revision}</span></button>)}</div>
      {selected && <div className="min-w-0 space-y-3">
        <p className="rounded-md bg-bg-elevated px-3 py-2 text-xs text-text-secondary">模块：{selectedDomain}</p>
        <section className="rounded-md bg-bg-elevated p-3"><h3 className="text-xs font-semibold text-text-secondary">用户问题</h3><p className="mt-1 whitespace-pre-wrap text-sm text-text-primary">{selected.payload_json.input?.query || '无问题文本'}</p><h3 className="mt-3 text-xs font-semibold text-text-secondary">原始建议事实与来源</h3><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-text-secondary">{JSON.stringify({ proposed_facts: selected.payload_json.proposed_facts, proposed_evidence: selected.payload_json.proposed_evidence, source_ref: selected.payload_json.source_ref }, null, 2)}</pre></section>
        <label className="block text-sm font-medium text-text-primary">审核后的标准事实 JSON<textarea aria-label="审核后的标准事实 JSON" value={expectedText} onChange={event => setExpectedText(event.target.value)} className={`${control} mt-1 min-h-40 w-full font-mono text-xs`} /></label>
        {selectedDomain === 'rag' ? <label className="block text-sm font-medium text-text-primary">原文证据锚点 JSON<textarea aria-label="原文证据锚点 JSON" value={evidenceText} onChange={event => setEvidenceText(event.target.value)} className={`${control} mt-1 min-h-36 w-full font-mono text-xs`} /><span className="mt-1 block text-xs font-normal text-text-muted">每项包含 kb_id、doc_id、doc_version 和 quote。不得填写自制校验哈希。</span></label> : <p className="rounded-md border border-border-subtle p-3 text-xs text-text-secondary">当前模块不使用 KB 证据锚点；提交时服务端会按该模块契约校验标准结果。</p>}
        <label className="block text-sm font-medium text-text-primary">审核理由<textarea required value={rationale} onChange={event => setRationale(event.target.value)} className={`${control} mt-1 min-h-20 w-full`} /></label>
        <div className="flex flex-wrap gap-2"><button type="button" disabled={busy || !rationale.trim()} onClick={() => void review('approved')} className={`${control} bg-accent text-white`}>{busy ? '保存中…' : '核验并通过'}</button><button type="button" disabled={busy || !rationale.trim()} onClick={() => void review('rejected')} className={control}>驳回候选</button></div>
      </div>}
    </div>}
  </section>
}
