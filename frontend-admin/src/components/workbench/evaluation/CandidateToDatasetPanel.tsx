'use client'

import { useState } from 'react'
import type { DatasetCandidate } from '@/api/evaluation'
import { evaluationAssets } from '@/api/evaluation-assets'
import { DatasetCaseForm, type DatasetCaseDraft } from './DatasetCaseContent'

const control = 'min-h-11 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm disabled:opacity-50'
export default function CandidateToDatasetPanel({ items }: { items: DatasetCandidate[] }) {
  const [candidate, setCandidate] = useState('')
  const [name, setName] = useState('')
  const [version, setVersion] = useState('1.0.0')
  const [draft, setDraft] = useState<DatasetCaseDraft>({ id: '', question: '', expected: { behavior: 'answer', evidence: [{ doc_id: '', quote: '' }], reference_answer: '', required_facts: [] } })
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  return <details className="rounded-lg border border-border-subtle p-5 text-sm"><summary className="cursor-pointer font-medium">从 Trace / 反馈候选创建评测集</summary><p className="my-3 text-xs leading-5 text-text-muted">选择候选，补齐可信答案和原文证据，再提交审核。</p>
    <div className="grid gap-4 sm:grid-cols-2"><label>来源候选<select className={`${control} mt-1 w-full`} aria-label="来源候选" value={candidate} onChange={e => { const item = items.find(i => i.candidate_id === e.target.value); setCandidate(e.target.value); setDraft(old => ({ ...old, id: item?.candidate_id || '', question: item?.question || '' })) }}><option value="">选择已脱敏 RAG 候选</option>{items.filter(i => i.redacted && i.module === 'rag').map(i => <option key={i.candidate_id} value={i.candidate_id}>{i.question.slice(0, 70)}</option>)}</select></label><label>新评测集名称<input className={`${control} mt-1 w-full`} aria-label="候选数据集名称" value={name} onChange={e => setName(e.target.value)} placeholder="如：客服常见问题回归" /></label></div>
    {candidate && <div className="my-5"><DatasetCaseForm item={draft} onChange={setDraft} lockQuestion showArchive={false} /></div>}
    <div className="mt-4 flex flex-wrap items-end gap-3"><label className="text-xs text-text-muted">版本<input className={`${control} mt-1 block w-32`} aria-label="候选数据集版本" value={version} onChange={e => setVersion(e.target.value)} /></label><button className={`${control} bg-accent text-white`} disabled={busy || !candidate || !name.trim()} onClick={() => void (async () => { setBusy(true); setError(''); try { await evaluationAssets.promoteCandidate(candidate, { dataset_key: `candidate_${candidate.replace(/[^a-z0-9]/gi, '_').toLowerCase()}`.slice(0, 64), name, version, schema_version: 'rag-eval/1.0', module: 'rag' }, draft.expected); setMessage('已提交到上方待审核队列，请刷新后核验并发布') } catch (e) { setError(e instanceof Error ? e.message : '转换失败') } finally { setBusy(false) } })()}>{busy ? '正在提交…' : '创建评测集并提交审核'}</button></div>{error && <p role="alert" className="mt-3 text-red-700">{error}</p>}{message && <p role="status" className="mt-3">{message}</p>}
  </details>
}
