'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { createIdempotencyKey } from '@/api/client'
import { evaluationService, type EvaluationV2Candidate, type EvaluationV2EvidenceClaim } from '@/api/evaluation'
import { EvalBadge, EvalButton, EvalCard, EvalEmpty, EvalField, EvalInfoBox, EvalPageHeader, EvalToolbar, evalControl } from './evalUi'
import EvidenceSearchPicker from './EvidenceSearchPicker'

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
  const [behavior, setBehavior] = useState<'answer' | 'refuse'>('answer')
  const [referenceAnswer, setReferenceAnswer] = useState('')
  const [factsText, setFactsText] = useState('')
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
    const expected = initialExpected(selected)
    setBehavior(expected.behavior === 'refuse' ? 'refuse' : 'answer')
    setReferenceAnswer(String(expected.reference_answer || ''))
    const facts = expected.required_facts
    setFactsText(Array.isArray(facts) ? facts.map(item => String(item)).join('\n') : '')
    setEvidenceText(JSON.stringify(initialEvidence(selected), null, 2))
    setRationale('')
  }, [selected?.candidate_id, selected?.revision])

  const buildExpected = (): Record<string, unknown> => {
    const base = selected ? initialExpected(selected) : { ...emptyExpected }
    return {
      ...base,
      behavior,
      reference_answer: referenceAnswer,
      required_facts: factsText.split('\n').map(item => item.trim()).filter(Boolean),
    }
  }

  const review = async (verdict: 'approved' | 'rejected') => {
    if (!selected) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const expected = verdict === 'approved' ? buildExpected() : undefined
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

  return <div aria-labelledby="evaluation-v2-review-heading">
    <EvalPageHeader
      title="标注审核工作台"
      subtitle="审核人确认模块标准结果。RAG 回答样本必须提供 KB、文档版本与原文引用，服务端按当前可信权限重新核验；其他模块使用各自的离线字段契约，不接收知识库证据。"
      actions={<EvalButton onClick={() => void load()} disabled={loading}><RefreshCw size={14} /> 刷新队列</EvalButton>}
    />
    {error && <p role="alert" className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
    {message && <p role="status" className="mb-3 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-900">{message}</p>}
    <EvalToolbar>
      <input value={traceId} onChange={event => setTraceId(event.target.value)} className={evalControl + ' w-72'} placeholder="粘贴线上 Trace ID" aria-label="线上 Trace ID" />
      <EvalButton variant="primary" onClick={() => void intakeTrace()} disabled={busy || !traceId.trim()}>从 Trace 创建候选</EvalButton>
      <span className="text-xs text-text-muted">服务端会再次检查 Trace 可见性；请求体不能指定租户或审核身份。</span>
    </EvalToolbar>
    {loading && items.length === 0 ? <p role="status" className="text-sm text-text-secondary">正在读取持久化候选…</p> : items.length === 0 ? <EvalCard><EvalEmpty>V2 审核队列为空。请在数据集中心预检并导入候选。</EvalEmpty></EvalCard> : <div className="grid gap-4 xl:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.5fr)]">
      <EvalCard title="候选队列" actions={<span className="rounded-md bg-accent-soft px-2 py-1 text-[11px] font-bold text-accent">{items.length}</span>} bodyClassName="max-h-[36rem] overflow-auto p-0">
        {items.map(item => (
          <button key={item.candidate_id} type="button" aria-pressed={selectedId === item.candidate_id} onClick={() => setSelectedId(item.candidate_id)} className={'block w-full border-b border-border-subtle p-4 text-left last:border-b-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ' + (selectedId === item.candidate_id ? 'border-l-[3px] border-l-accent bg-accent-soft' : 'hover:bg-bg-hover')}>
            <span className="block line-clamp-2 text-sm font-medium text-text-primary">{item.payload_json.input?.query || '无问题文本'}</span>
            <span className="mt-1.5 flex items-center gap-2 text-xs text-text-muted">
              {String(item.payload_json.source_ref?.dataset_key || item.payload_json.source_ref?.trace_id || item.candidate_id).slice(0, 24)} · 修订 {item.revision}
              <EvalBadge text="待审核" tone="warn" />
            </span>
          </button>
        ))}
      </EvalCard>
      {selected && <div className="min-w-0 space-y-4">
        <EvalCard
          title={'候选标注 · ' + selected.candidate_id.slice(0, 8)}
          actions={<EvalBadge text={selectedDomain.toUpperCase() + ' 模块'} tone="blue" />}
        >
          <div className="mb-3 flex flex-wrap gap-2">
            <span className="rounded bg-bg-elevated px-1.5 py-1 text-[11px] text-text-secondary">来源：{String(selected.payload_json.source_ref?.dataset_key || 'Trace 导入')}</span>
            <span className="rounded bg-bg-elevated px-1.5 py-1 text-[11px] text-text-secondary">候选修订 {selected.revision}</span>
          </div>
          <EvalField label="用户问题（只读，修改问题请创建修订候选）">
            <p className="whitespace-pre-wrap rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-sm text-text-primary">{selected.payload_json.input?.query || '无问题文本'}</p>
          </EvalField>
          <div className="mt-4 grid gap-4 sm:grid-cols-[minmax(0,1fr)_160px]">
            <EvalField label="候选参考答案（需要人工校验）">
              <textarea aria-label="候选参考答案" value={referenceAnswer} onChange={event => setReferenceAnswer(event.target.value)} className={evalControl + ' min-h-24 w-full'} />
            </EvalField>
            <EvalField label="回答行为">
              <select aria-label="回答行为" value={behavior} onChange={event => setBehavior(event.target.value === 'refuse' ? 'refuse' : 'answer')} className={evalControl + ' w-full'}>
                <option value="answer">回答样本</option>
                <option value="refuse">拒答样本</option>
              </select>
            </EvalField>
          </div>
          <div className="mt-4">
            <EvalField label="必须包含的事实 · 每行一个">
              <textarea aria-label="必须包含的事实" value={factsText} onChange={event => setFactsText(event.target.value)} className={evalControl + ' min-h-24 w-full'} placeholder={'提前三个工作日提交审批\n部门负责人批准后生效'} />
            </EvalField>
          </div>
        </EvalCard>
        <EvalCard title="可信原文证据" subtitle="服务端按当前可信权限重新核验；不得填写自制校验哈希。">
          {selectedDomain === 'rag' ? (
            <div className="block text-sm text-text-secondary">
              <span className="mb-1 block">原文证据锚点 JSON</span>
              <EvidenceSearchPicker onInsert={claim => setEvidenceText(current => {
                try {
                  const parsed = JSON.parse(current) as unknown[]
                  return JSON.stringify([...(Array.isArray(parsed) ? parsed : []), claim], null, 2)
                } catch {
                  return JSON.stringify([claim], null, 2)
                }
              })} />
              <textarea aria-label="原文证据锚点 JSON" value={evidenceText} onChange={event => setEvidenceText(event.target.value)} className={evalControl + ' mt-1.5 min-h-36 w-full font-mono text-xs'} />
              <span className="mt-1 block text-xs text-text-muted">每项包含 kb_id、doc_id、doc_version 和 quote。</span>
            </div>
          ) : (
            <EvalInfoBox>当前模块不使用 KB 证据锚点；提交时服务端会按该模块契约校验标准结果。</EvalInfoBox>
          )}
          <div className="mt-4">
            <EvalField label="审核说明 / 驳回理由">
              <textarea aria-label="审核理由" required value={rationale} onChange={event => setRationale(event.target.value)} className={evalControl + ' min-h-20 w-full'} placeholder="例如：已核对原文段落、数字和适用条件" />
            </EvalField>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <EvalButton variant="danger" disabled={busy || !rationale.trim()} onClick={() => void review('rejected')}>驳回候选</EvalButton>
            <EvalButton variant="primary" disabled={busy || !rationale.trim()} onClick={() => void review('approved')}>{busy ? '保存中…' : '确认事实并批准'}</EvalButton>
          </div>
        </EvalCard>
      </div>}
    </div>}
  </div>
}
