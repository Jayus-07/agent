'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { evaluationAssets, downloadEvalTemplate, type ImportedDataset, type ImportValidation } from '@/api/evaluation-assets'
import { atLeast } from '@/lib/auth'
import { useToast } from '@/components/shared/Toast'
import DatasetRevisionEditor from './DatasetRevisionEditor'
import DatasetSynthesisPanel from './DatasetSynthesisPanel'
import { DatasetCaseView, type DatasetCaseDraft } from './DatasetCaseContent'
import NewDatasetForm from './NewDatasetForm'

const control = 'min-h-11 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-blue-300 disabled:opacity-50'
const labels: Record<string, string> = { pending_review: '待审核', approved: '已发布', rejected: '已驳回' }

export default function ImportedDatasetsPanel({ review = false }: { review?: boolean }) {
  const toast = useToast()
  const [items, setItems] = useState<ImportedDataset[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [version, setVersion] = useState('1.0.0')
  const [validation, setValidation] = useState<ImportValidation | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reason, setReason] = useState('')
  const [ready, setReady] = useState(false)
  const [details, setDetails] = useState<Record<string, unknown[]>>({})
  const [mapping, setMapping] = useState('{}')
  const [schema, setSchema] = useState('{}')
  const [showUpload, setShowUpload] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const load = useCallback(async () => {
    try { setItems(await evaluationAssets.list()); setError('') }
    catch (e) { setError(e instanceof Error ? e.message : '加载评测集失败') }
  }, [])
  useEffect(() => { setReady(true); if (atLeast('super_admin')) void load() }, [load])
  if (!ready) return <p className="p-4 text-xs text-text-muted">加载评测集…</p>
  if (!atLeast('super_admin')) return <p className="p-4 text-xs text-text-muted">共享业务题集由平台超级管理员导入、审核和关联；内置基准可在上方切换查看。</p>

  const manifest = { schema_version: 'rag-eval/1.0', dataset_key: key, name, version, module: 'rag' }
  const configuredManifest = () => ({ ...manifest, csv_mapping: JSON.parse(mapping), case_schema: JSON.parse(schema) })
  const act = async (operation: () => Promise<void>) => {
    setBusy(true)
    try { await operation() } catch (e) { setError(e instanceof Error ? e.message : '操作失败') } finally { setBusy(false) }
  }
  const shown = items.filter(item => (!review || item.review.decision === 'pending_review') && (status === 'all' || item.review.decision === status) && `${item.manifest.name} ${item.manifest.dataset_key}`.toLowerCase().includes(search.toLowerCase()))
  return <section className="space-y-4" aria-label={review ? '导入审核' : '业务评测集'}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="text-sm font-semibold">{review ? '审核导入题集' : '业务评测集'}</h2><p className="mt-1 text-xs text-text-muted">{review ? '核验问题、原文证据和参考答案后发布不可变版本。' : '上传题集，经审核后关联知识库并运行检索评测。每个版本最多 100 题、5 MB。'}</p></div>
      <div className="flex flex-wrap gap-2">{!review && <><button className={control} onClick={downloadEvalTemplate}>下载模板</button><button className={control} aria-expanded={showUpload} onClick={() => { setShowUpload(old => !old); setShowNew(false) }}>{showUpload ? '收起导入' : '批量导入'}</button><button className={`${control} bg-blue-600 text-white`} aria-expanded={showNew} onClick={() => { setShowNew(old => !old); setShowUpload(false) }}>{showNew ? '收起新建' : '新建评测集'}</button></>}<button className={control} disabled={busy} onClick={() => void load()}>刷新</button></div>
    </div>
    {error && <div role="alert" className="rounded-md bg-red-50 p-3 text-xs text-red-700">{error}</div>}
    {!review && showNew && <NewDatasetForm onSaved={load} />}
    {!review && showUpload && <div className="space-y-4 rounded-lg border border-border-subtle p-5">
      <label className="block text-xs">选择 JSON / JSONL / CSV 文件<input aria-label="评测集文件" type="file" accept=".json,.jsonl,.csv" disabled={busy} onChange={e => { setFile(e.target.files?.[0] || null); setValidation(null); setError('') }} className="mt-2 block w-full text-xs" /></label>
      <p className="text-xs text-text-muted">JSON 包含 manifest 与 cases；JSONL 每行一题；CSV 使用标准表头，expected/tags/splits 列填写 JSON，并填写下面的清单信息。JSON 文件以文件内清单为准。</p>
      {file && !file.name.toLowerCase().endsWith('.json') && <div className="grid gap-3 sm:grid-cols-3">{[
        ['数据集标识', key, setKey, 'policy_golden'], ['数据集名称', name, setName, '制度评测集'], ['版本', version, setVersion, '1.0.0'],
      ].map(([label, value, setter, placeholder]) => <label key={String(label)} className="text-sm">{String(label)}<input className={`${control} mt-1 w-full`} disabled={busy} value={String(value)} placeholder={String(placeholder)} onChange={e => { (setter as (v: string) => void)(e.target.value); setValidation(null) }} /></label>)}</div>}
      <div className="flex gap-2">
        <button className={control} disabled={!file || busy} onClick={() => void act(async () => { setError(''); setValidation(await evaluationAssets.validate(file!, configuredManifest())) })}>{busy ? '处理中…' : '校验并预览'}</button>
        <button className={`${control} bg-blue-600 text-white`} disabled={!file || busy || !validation?.valid} onClick={() => void act(async () => { await evaluationAssets.import(file!, configuredManifest()); toast.success('题集已提交审核'); setValidation(null); await load() })}>提交审核</button>
      </div>
      <details className="text-xs"><summary className="cursor-pointer">CSV 列映射与附加 Schema</summary><p className="my-2 text-text-muted">CSV 映射示例：{"{\"id\":\"题号\",\"question\":\"问题\",\"expected\":\"预期JSON\"}"}。附加 Schema 校验每条标准化用例，禁止引用和正则；JSON 文件使用文件内 manifest。</p><label>CSV 列映射<textarea aria-label="CSV 列映射" className={`${control} my-1 block w-full font-mono`} value={mapping} onChange={e => { setMapping(e.target.value); setValidation(null) }} /></label><label>附加 JSON Schema<textarea aria-label="附加 JSON Schema" className={`${control} my-1 block w-full font-mono`} value={schema} onChange={e => { setSchema(e.target.value); setValidation(null) }} /></label></details>
      {validation && <div role="status" className="text-xs"><p>{validation.valid ? `格式校验通过，共 ${validation.case_count} 题；尚未核验知识库证据。` : '校验未通过，请修正后重新校验。'}</p>{validation.errors.map((e, i) => <p className="mt-1 text-red-700" key={i}>第 {e.line} 行 · {e.field}：{e.message}</p>)}{validation.preview?.map(item => <p className="mt-2" key={item.id}>{item.id}：{item.question}</p>)}</div>}
    </div>}
    {!review && <DatasetSynthesisPanel />}
    {!review && <div className="flex flex-wrap items-center gap-3 border-t border-border-subtle pt-5"><input aria-label="搜索评测集" className={`${control} min-w-52 flex-1`} value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索名称或标识" /><select aria-label="审核状态筛选" className={control} value={status} onChange={e => setStatus(e.target.value)}><option value="all">全部状态</option><option value="pending_review">待审核</option><option value="approved">已发布</option><option value="rejected">已驳回</option></select><span className="text-xs text-text-muted">{shown.length} 个版本</span></div>}
    {review && <label className="block text-xs">审核说明<input className={`${control} ml-2 w-full sm:w-96`} value={reason} onChange={e => setReason(e.target.value)} placeholder="填写核验依据或驳回原因" /></label>}
    <div className="divide-y divide-border-subtle rounded-lg border border-border-subtle">{shown.map(item => <article key={item.import_id} className="p-4">
      <div className="flex flex-wrap justify-between gap-2"><div><h3 className="text-sm font-medium">{item.manifest.name} <span className="text-xs text-text-muted">v{item.manifest.version} · {item.active_case_count ?? item.case_count} 题可用</span></h3><p className="mt-1 text-xs text-text-muted">{item.manifest.dataset_key} · {labels[item.review.decision] || item.review.decision}</p></div>
        {review ? <div className="flex gap-2">{['approved', 'rejected'].map(decision => <button className={control} disabled={busy || !reason.trim() || (decision === 'approved' && !details[item.import_id])} key={decision} onClick={() => void act(async () => { await evaluationAssets.review(item.import_id, decision, reason); toast.success(decision === 'approved' ? '已发布不可变版本' : '已驳回'); await load() })}>{decision === 'approved' ? '核验并发布' : '驳回'}</button>)}</div> : item.review.decision === 'approved' ? <Link className="text-xs text-blue-600" href={`/evaluations/center?tab=bindings&import_id=${item.import_id}`}>关联知识库并运行</Link> : <Link className="text-xs text-blue-600" href="/evaluations/center?tab=reviews">前往审核</Link>}
      </div>
      <details className="mt-3 text-sm"><summary className="cursor-pointer text-text-secondary">查看题目样本</summary><div className="mt-3 divide-y divide-border-subtle">{item.preview.map(c => <div className="py-3" key={c.id}><DatasetCaseView item={c as DatasetCaseDraft} /></div>)}</div><details className="mt-2 text-xs text-text-muted"><summary className="cursor-pointer">版本标识</summary><p className="my-2 break-all">{item.import_id}<br />{item.content_hash}</p></details></details>
      <button className={`${control} mt-3`} disabled={busy} onClick={() => void act(async () => { const detail = await evaluationAssets.detail(item.import_id); setDetails(old => ({ ...old, [item.import_id]: detail.cases })) })}>读取全部 {item.case_count} 题</button>
      {details[item.import_id] && <div className="mt-2"><button className={control} onClick={() => { const url = URL.createObjectURL(new Blob([JSON.stringify({ manifest: item.manifest, cases: details[item.import_id] }, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = `${item.manifest.dataset_key}-${item.manifest.version}.json`; a.click(); URL.revokeObjectURL(url) }}>导出此版本</button><div className="mt-3 max-h-[36rem] divide-y divide-border-subtle overflow-auto rounded-md bg-slate-50 px-4">{(details[item.import_id] as DatasetCaseDraft[]).map(c => <div key={c.id} className="py-4"><DatasetCaseView item={c} /></div>)}</div></div>}
      {!review && <DatasetRevisionEditor item={item} done={load} />}
    </article>)}{shown.length === 0 && <p className="p-8 text-center text-xs text-text-muted">{review ? '暂无待审核的导入题集' : '暂无评测集。点击“新建评测集”直接填写，或批量导入已有题目。'}</p>}</div>
  </section>
}
