'use client'

import { useEffect, useRef, useState } from 'react'
import {
  evaluationService,
  type EvaluationV2HumanReview,
  type EvaluationV2HumanReviewCollection,
} from '@/api/evaluation'
import { createIdempotencyKey } from '@/api/client'

const labels: Array<{ value: EvaluationV2HumanReview['label']; text: string }> = [
  { value: 'correct', text: '正确' },
  { value: 'partially_correct', text: '部分正确' },
  { value: 'incorrect', text: '错误' },
  { value: 'unsafe', text: '存在安全问题' },
  { value: 'unverifiable', text: '无法核验' },
]

export default function EvaluationHumanReviewButton({ runId, caseId }: { runId: string; caseId: string }) {
  const [open, setOpen] = useState(false)
  const [collection, setCollection] = useState<EvaluationV2HumanReviewCollection | null>(null)
  const [evaluatorId, setEvaluatorId] = useState('')
  const [scores, setScores] = useState<Record<string, number>>({})
  const [label, setLabel] = useState<EvaluationV2HumanReview['label']>('correct')
  const [rationale, setRationale] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const pendingRequest = useRef<{ key: string; requestId: string } | null>(null)

  useEffect(() => {
    if (!open) return
    let active = true
    setLoading(true)
    setError('')
    void evaluationService.getV2HumanReviews(runId, caseId)
      .then(value => {
        if (!active) return
        setCollection(value)
        const selected = value.evaluators.find(item => item.evaluator_version_id === evaluatorId) || value.evaluators[0]
        setEvaluatorId(selected?.evaluator_version_id || '')
        setScores(selected ? Object.fromEntries(selected.config.criteria.map(item => [item.key, 0])) : {})
      })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : '读取人工 Rubric 与复核历史失败') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [open, runId, caseId])

  const evaluator = collection?.evaluators.find(item => item.evaluator_version_id === evaluatorId)

  const submit = async () => {
    if (!evaluator) { setError('运行快照未绑定可用的人工评分 Rubric。'); return }
    const cleanRationale = rationale.trim()
    if (cleanRationale.length < 8 || cleanRationale.length > 2000) { setError('复核理由需为 8 到 2000 个非空字符。'); return }
    const key = JSON.stringify({ evaluatorId, label, scores, rationale: cleanRationale })
    const pending = pendingRequest.current?.key === key
      ? pendingRequest.current
      : { key, requestId: createIdempotencyKey() }
    pendingRequest.current = pending
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const created = await evaluationService.createV2HumanReview(runId, caseId, {
        evaluator_version_id: evaluator.evaluator_version_id,
        request_id: pending.requestId,
        label,
        scores,
        rationale: cleanRationale,
      })
      pendingRequest.current = null
      setCollection(current => current ? { ...current, reviews: [created, ...current.reviews.filter(item => item.review_id !== created.review_id)] } : current)
      setMessage(`人工复核已追加，综合分 ${Number(created.overall_score).toFixed(3)}。机器评分与 Gate 未改变。`)
      setRationale('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '提交人工复核失败')
    } finally { setSaving(false) }
  }

  return <div className="inline-block">
    <button type="button" onClick={() => setOpen(value => !value)} aria-expanded={open} className="text-accent">{open ? '收起人工复核' : '人工复核'}</button>
    {open && <section className="mt-3 min-w-[360px] max-w-2xl space-y-3 rounded-md border border-border-subtle bg-surface-base p-4" aria-label={`用例 ${caseId} 人工复核`}>
      <div><h4 className="font-medium text-text-primary">人工复核 · {caseId}</h4><p className="mt-1 text-xs text-text-secondary">评分绑定运行时冻结的 Human Rubric，形成独立校准记录，不重算自动 Gate。</p></div>
      {loading && <p role="status" className="text-sm text-text-secondary">读取 Rubric…</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {message && <p role="status" className="text-sm text-text-secondary">{message}</p>}
      {!loading && collection && collection.evaluators.length === 0 && <p className="rounded bg-bg-elevated p-3 text-sm text-text-secondary">此 Run 快照未绑定人工复核 Rubric。需要在评测方案版本中添加 Human Rubric 后启动新 Run。</p>}
      {evaluator && <>
        {collection && collection.evaluators.length > 1 && <label className="block text-xs text-text-secondary">Rubric<select value={evaluatorId} onChange={event => { const selected = collection.evaluators.find(item => item.evaluator_version_id === event.target.value); setEvaluatorId(event.target.value); setScores(selected ? Object.fromEntries(selected.config.criteria.map(item => [item.key, 0])) : {}) }} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm">{collection.evaluators.map(item => <option key={item.evaluator_version_id} value={item.evaluator_version_id}>{item.evaluator_key}@{item.version}</option>)}</select></label>}
        <div className="grid gap-2 sm:grid-cols-2">{evaluator.config.criteria.map(criterion => <label key={criterion.key} className="text-xs text-text-secondary">{criterion.label} · 权重 {Math.round(criterion.weight * 100)}%<input aria-label={`${criterion.label}评分`} type="number" min={0} max={1} step={0.05} value={scores[criterion.key] ?? 0} onChange={event => setScores(current => ({ ...current, [criterion.key]: Number(event.target.value) }))} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm" /></label>)}</div>
        <label className="block text-xs text-text-secondary">复核结论<select aria-label="复核结论" value={label} onChange={event => setLabel(event.target.value as EvaluationV2HumanReview['label'])} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm">{labels.map(item => <option key={item.value} value={item.value}>{item.text}</option>)}</select></label>
        <label className="block text-xs text-text-secondary">复核理由<textarea aria-label="复核理由" value={rationale} onChange={event => setRationale(event.target.value)} minLength={8} maxLength={2000} rows={3} className="mt-1 w-full rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm" /></label>
        <button type="button" onClick={() => void submit()} disabled={saving || loading} className="min-h-10 rounded-md bg-accent px-4 text-sm font-medium text-white disabled:opacity-50">{saving ? '提交中…' : '追加人工评分'}</button>
      </>}
      {collection?.reviews.length ? <ol className="divide-y divide-border-subtle border-t border-border-subtle">{collection.reviews.map(review => <li key={review.review_id} className="py-3 text-xs"><div className="flex justify-between gap-2"><span className="font-medium text-text-primary">{labels.find(item => item.value === review.label)?.text || review.label} · {Number(review.overall_score).toFixed(3)}</span><time className="text-text-muted">{new Date(review.reviewed_at).toLocaleString('zh-CN')}</time></div><p className="mt-1 text-text-secondary">{review.reviewed_by} · {review.rationale}</p></li>)}</ol> : collection && evaluator ? <p className="border-t border-border-subtle pt-3 text-xs text-text-secondary">还没有人工复核记录。</p> : null}
    </section>}
  </div>
}
