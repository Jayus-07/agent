'use client'

import { useState } from 'react'
import Link from 'next/link'
import { evaluationAssets } from '@/api/evaluation-assets'
import { DatasetCaseForm, caseEditingError, type DatasetCaseDraft } from './DatasetCaseContent'

const control = 'min-h-11 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm disabled:opacity-50'
const initialCase = (): DatasetCaseDraft => ({ id: 'CASE-001', question: '', expected: { behavior: 'answer', evidence: [{ doc_id: '', quote: '' }], reference_answer: '', required_facts: [] } })
export default function NewDatasetForm({ onSaved }: { onSaved: () => Promise<void> }) {
  const [name, setName] = useState('')
  const [key, setKey] = useState('')
  const [cases, setCases] = useState<DatasetCaseDraft[]>([initialCase()])
  const [index, setIndex] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  if (saved) return <section aria-label="新建评测集" className="space-y-4 rounded-lg border border-border-subtle p-5"><p role="status" className="text-sm text-emerald-700">评测集已保存并提交审核。</p><div className="flex flex-wrap items-center gap-4"><Link className="text-sm text-blue-600 underline" href="/evaluations/center?tab=reviews">前往核验并发布</Link><button className={control} onClick={() => { setName(''); setKey(''); setCases([initialCase()]); setIndex(0); setError(''); setSaved(false) }}>继续新建评测集</button></div></section>
  return <section aria-label="新建评测集" className="space-y-5 rounded-lg border border-border-subtle p-5"><div><h3 className="text-base font-medium">新建评测集</h3><p className="mt-1 text-xs text-text-muted">直接填写问题、可信答案和原文证据，无需编写文件。大量题目可使用批量导入。</p></div><label className="block text-sm">评测集名称<input autoFocus aria-label="新评测集名称" className={`${control} mt-1 w-full`} value={name} onChange={e => { setName(e.target.value); setSaved(false) }} placeholder="如：员工制度回归题集" /></label>
    {cases.length > 1 && <label className="block text-sm">当前题目<select aria-label="新建题集当前题目" className={`${control} ml-2 max-w-full`} value={index} onChange={e => setIndex(Number(e.target.value))}>{cases.map((item, i) => <option key={item.id} value={i}>{i + 1}. {item.question.slice(0, 60) || '待填写问题'}</option>)}</select></label>}
    <DatasetCaseForm item={cases[index]} showArchive={false} onChange={value => { setCases(old => old.map((item, i) => i === index ? value : item)); setSaved(false) }} />
    <button className="text-sm text-blue-600" disabled={busy || cases.length >= 100} onClick={() => { setCases(old => [...old, { ...initialCase(), id: `CASE-${String(old.length + 1).padStart(3, '0')}` }]); setIndex(cases.length); setSaved(false) }}>＋ 再添加一道题</button>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}{saved && <p role="status" className="text-sm text-emerald-700">评测集已保存并提交审核。<Link className="ml-2 underline" href="/evaluations/center?tab=reviews">前往核验并发布</Link></p>}
    <div className="flex flex-wrap items-center gap-3 border-t border-border-subtle pt-4"><button className={`${control} bg-blue-600 text-white`} disabled={busy || !name.trim() || saved} onClick={() => void (async () => {
      setBusy(true); setError('')
      try {
        const badIndex = cases.findIndex(item => caseEditingError(item))
        if (badIndex >= 0) { setIndex(badIndex); throw new Error(caseEditingError(cases[badIndex])!) }
        const datasetKey = key || `dataset_${Date.now()}`; setKey(datasetKey)
        const manifest = { schema_version: 'rag-eval/1.0', dataset_key: datasetKey, name: name.trim(), version: '1.0.0', module: 'rag' }
        const file = new File([JSON.stringify({ manifest, cases })], 'dataset.json', { type: 'application/json' })
        const checked = await evaluationAssets.validate(file, manifest)
        if (!checked.valid) throw new Error(checked.errors.map(e => `第 ${e.line} 题：${e.message}`).join('；'))
        await evaluationAssets.import(file, manifest); setSaved(true); await onSaved()
      } catch (e) { setError(e instanceof Error ? e.message : '保存失败，请重试') } finally { setBusy(false) }
    })()}>{busy ? '正在保存…' : '保存并提交审核'}</button><span className="text-xs text-text-muted">共 {cases.length} 题 · 新版本 1.0.0</span></div>
  </section>
}
