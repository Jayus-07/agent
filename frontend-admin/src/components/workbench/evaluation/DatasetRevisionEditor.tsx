'use client'

import { useState } from 'react'
import { evaluationAssets, type ImportedDataset } from '@/api/evaluation-assets'
import { DatasetCaseForm, caseEditingError, type DatasetCaseDraft } from './DatasetCaseContent'

const control = 'min-h-11 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm disabled:opacity-50'
export default function DatasetRevisionEditor({ item, done }: { item: ImportedDataset; done: () => Promise<void> }) {
  const [cases, setCases] = useState<DatasetCaseDraft[] | null>(null)
  const [selected, setSelected] = useState('')
  const [version, setVersion] = useState('')
  const [label, setLabel] = useState('')
  const [other, setOther] = useState('')
  const [result, setResult] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [originalIds, setOriginalIds] = useState<string[]>([])
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError('')
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : '操作失败') } finally { setBusy(false) }
  }
  const current = cases?.find(c => c.id === selected)
  return <details className="mt-4 border-t border-border-subtle pt-3 text-sm"><summary className="cursor-pointer text-text-secondary">编辑题目与管理版本</summary>
    <p className="my-3 text-xs text-text-muted">修改会保存为待审核的新版本，历史评测保持原样。</p>
    {error && <p role="alert" className="mb-3 text-red-700">{error}</p>}
    {!cases && <button className={control} disabled={busy} onClick={() => void act(async () => { const d = await evaluationAssets.detail(item.import_id); const loaded = d.cases as DatasetCaseDraft[]; setOriginalIds(loaded.map(c => c.id)); setCases(loaded); setSelected(loaded[0]?.id || ''); const parts = item.manifest.version.split('.').map(Number); setVersion(`${parts[0]}.${parts[1]}.${(parts[2] || 0) + 1}`) })}>编辑题目</button>}
    {cases && <div className="space-y-4"><label className="block text-xs text-text-muted">选择要修改的题目<select aria-label="选择编辑题目" className={`${control} mt-1 w-full`} value={selected} onChange={e => setSelected(e.target.value)}>{cases.map(c => <option key={c.id} value={c.id}>{c.archived ? '已归档 · ' : ''}{c.id} — {c.question.slice(0, 70)}</option>)}</select></label>
      {current && <DatasetCaseForm item={current} onChange={value => setCases(old => old!.map(c => c.id === selected ? value : c))} />}
      <button className="text-sm text-blue-600" onClick={() => { const id = `CASE-${Date.now()}`; setCases(old => [...old!, { id, question: '', expected: { behavior: 'answer', evidence: [{ doc_id: '', quote: '' }], reference_answer: '', required_facts: [] } }]); setSelected(id) }}>＋ 新增题目</button>
      <div className="flex flex-wrap items-end gap-3 border-t border-border-subtle pt-4"><label className="text-xs text-text-muted">新版本号<input aria-label="修订版本号" className={`${control} mt-1 block w-36`} value={version} onChange={e => setVersion(e.target.value)} /></label><button className={`${control} bg-blue-600 text-white`} disabled={busy || !version} onClick={() => void act(async () => { const invalid = cases.find(c => caseEditingError(c)); if (invalid) { setSelected(invalid.id); throw new Error(caseEditingError(invalid)!); } const changes: Record<string, unknown> = Object.fromEntries(cases.map(c => [c.id, c])); for (const id of originalIds) if (!cases.some(c => c.id === id)) changes[id] = null; await evaluationAssets.revise(item.import_id, version, changes); setResult('新版本已保存，请前往候选审核'); setCases(null); await done() })}>{busy ? '正在保存…' : '保存新版本并提交审核'}</button></div>
      <details className="text-xs text-text-muted"><summary className="cursor-pointer">高级：查看完整条目 JSON</summary><pre className="mt-2 max-h-64 overflow-auto bg-slate-50 p-3">{JSON.stringify(cases, null, 2)}</pre></details>
    </div>}
    <details className="mt-4 text-xs text-text-muted"><summary className="cursor-pointer">高级：固定标签与版本差异</summary><div className="mt-3 flex flex-wrap gap-2"><input aria-label="版本标签" className={control} value={label} onChange={e => setLabel(e.target.value)} placeholder="如 prod-202610" /><button className={control} disabled={busy || !label || item.review.decision !== 'approved'} onClick={() => void act(async () => { await evaluationAssets.label(item.import_id, label); setResult('标签已固定到此审核版本') })}>固定标签</button><input aria-label="比较版本 ID" className={control} value={other} onChange={e => setOther(e.target.value)} placeholder="另一个版本的 import_id" /><button className={control} disabled={busy || !other} onClick={() => void act(async () => { setResult(JSON.stringify(await evaluationAssets.diff(item.import_id, other), null, 2)) })}>查看差异</button></div></details>
    {result && <p role="status" className="mt-3 whitespace-pre-wrap text-xs text-text-secondary">{result}</p>}
  </details>
}
