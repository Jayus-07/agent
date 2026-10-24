'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, Download, RefreshCw } from 'lucide-react'
import { createIdempotencyKey } from '@/api/client'
import {
  evaluationService,
  type EvaluationV2Candidate,
  type EvaluationV2Dataset,
  type EvaluationV2DatasetCoverage,
  type EvaluationV2DatasetVersion,
  type EvaluationV2DatasetVersionDiff,
  type EvaluationV2ImportPreview,
} from '@/api/evaluation'
import EmptyState from '@/components/shared/EmptyState'
import ImportTemplateButtons from './ImportTemplateButtons'

const control = 'min-h-11 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm text-text-primary disabled:opacity-50'
const coverageLabels = {
  category: '类别',
  scenario: '场景',
  risk_level: '风险等级',
  difficulty: '难度',
} as const
const domainOptions = [
  { value: 'rag', label: 'RAG 知识检索' },
  { value: 'sql', label: 'SQL 数据分析' },
  { value: 'cs', label: '客服' },
  { value: 'travel', label: '旅游规划' },
  { value: 'travel-provider', label: '旅游 Provider' },
  { value: 'travel-commerce', label: '旅游电商' },
  { value: 'travel-booking', label: '旅游预订' },
  { value: 'planner', label: '规划器' },
  { value: 'e2e', label: '端到端' },
]

export default function EvaluationV2DatasetPanel({ onGoReview }: { onGoReview?: () => void }) {
  const [datasets, setDatasets] = useState<EvaluationV2Dataset[]>([])
  const [datasetSearch, setDatasetSearch] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [versions, setVersions] = useState<EvaluationV2DatasetVersion[]>([])
  const [coverage, setCoverage] = useState<EvaluationV2DatasetCoverage | null>(null)
  const [coverageLoading, setCoverageLoading] = useState(false)
  const [coverageError, setCoverageError] = useState('')
  const [baseVersionId, setBaseVersionId] = useState('')
  const [targetVersionId, setTargetVersionId] = useState('')
  const [versionDiff, setVersionDiff] = useState<EvaluationV2DatasetVersionDiff | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  const [diffError, setDiffError] = useState('')
  const [approved, setApproved] = useState<EvaluationV2Candidate[]>([])
  const [selectedCases, setSelectedCases] = useState<string[]>([])
  const [revisionTargetCaseId, setRevisionTargetCaseId] = useState('')
  const [revisionBaseNumber, setRevisionBaseNumber] = useState(0)
  const [revisionQuery, setRevisionQuery] = useState('')
  const [revisionObservedAnswer, setRevisionObservedAnswer] = useState('')
  const [revisionLoading, setRevisionLoading] = useState(false)
  const [datasetKey, setDatasetKey] = useState('support_golden')
  const [datasetName, setDatasetName] = useState('客服知识评测集')
  const [domain, setDomain] = useState('rag')
  const [description, setDescription] = useState('')
  const [version, setVersion] = useState('1.0.0')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<EvaluationV2ImportPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [exportingVersionId, setExportingVersionId] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const selected = datasets.find(item => item.dataset_id === selectedId)
  const filteredDatasets = useMemo(() => {
    const query = datasetSearch.trim().toLocaleLowerCase('zh-CN')
    if (!query) return datasets
    return datasets.filter(item => [item.name, item.dataset_key, item.domain]
      .some(value => value.toLocaleLowerCase('zh-CN').includes(query)))
  }, [datasetSearch, datasets])
  const availableCases = useMemo(() => approved.filter(item =>
    item.promoted_case_id && String(item.payload_json.source_ref?.dataset_key || '') === selected?.dataset_key,
  ).reduce<EvaluationV2Candidate[]>((latest, item) => {
    const existingIndex = latest.findIndex(candidate => candidate.promoted_case_id === item.promoted_case_id)
    if (existingIndex === -1) return [...latest, item]
    const itemRevision = Number(item.payload_json.verified_case_revision || 1)
    const existingRevision = Number(latest[existingIndex].payload_json.verified_case_revision || 1)
    if (itemRevision <= existingRevision) return latest
    const next = [...latest]
    next[existingIndex] = item
    return next
  }, []), [approved, selected?.dataset_key])
  const revisionTarget = availableCases.find(item => item.promoted_case_id === revisionTargetCaseId)

  useEffect(() => {
    if (!revisionTargetCaseId) {
      setRevisionBaseNumber(0)
      setRevisionQuery('')
      return
    }
    let active = true
    setRevisionLoading(true)
    evaluationService.getV2CaseLatestRevision(revisionTargetCaseId)
      .then(item => {
        if (!active) return
        setRevisionBaseNumber(item.revision)
        setRevisionQuery(item.input_json.query)
        setRevisionObservedAnswer('')
      })
      .catch(cause => {
        if (active) setError(cause instanceof Error ? cause.message : '读取 Case 最新修订失败')
      })
      .finally(() => { if (active) setRevisionLoading(false) })
    return () => { active = false }
  }, [revisionTargetCaseId])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [items, candidates] = await Promise.all([
        evaluationService.listV2Datasets(),
        evaluationService.listV2Candidates('approved'),
      ])
      setDatasets(items)
      setApproved(candidates)
      setSelectedId(current => items.some(item => item.dataset_id === current) ? current : items[0]?.dataset_id || '')
      if (items.length === 0) setVersions([])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取 V2 数据资产失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!selectedId) {
      setVersions([])
      setSelectedCases([])
      setVersionDiff(null)
      setBaseVersionId('')
      setTargetVersionId('')
      return
    }
    setVersions([])
    setVersionDiff(null)
    let active = true
    void evaluationService.listV2DatasetVersions(selectedId).then(items => {
      if (active) setVersions(items)
    }).catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : '读取数据集版本失败')
    })
    setSelectedCases([])
    return () => { active = false }
  }, [selectedId])

  useEffect(() => {
    if (!versions.length) {
      setBaseVersionId('')
      setTargetVersionId('')
      setVersionDiff(null)
      return
    }
    const versionIds = new Set(versions.map(item => item.dataset_version_id))
    const nextTargetId = versionIds.has(targetVersionId) ? targetVersionId : versions[0].dataset_version_id
    setTargetVersionId(nextTargetId)
    setBaseVersionId(current => versionIds.has(current) && current !== nextTargetId
      ? current
      : versions.find(item => item.dataset_version_id !== nextTargetId)?.dataset_version_id || nextTargetId)
  }, [versions, targetVersionId])

  useEffect(() => {
    if (!selectedId || !baseVersionId || !targetVersionId || baseVersionId === targetVersionId) {
      setVersionDiff(null)
      setDiffError('')
      setDiffLoading(false)
      return
    }
    let active = true
    setDiffLoading(true)
    setDiffError('')
    void evaluationService.compareV2DatasetVersions(selectedId, baseVersionId, targetVersionId)
      .then(result => { if (active) setVersionDiff(result) })
      .catch(cause => {
        if (active) {
          setVersionDiff(null)
          setDiffError(cause instanceof Error ? cause.message : '读取 Golden 版本差异失败')
        }
      })
      .finally(() => { if (active) setDiffLoading(false) })
    return () => { active = false }
  }, [selectedId, baseVersionId, targetVersionId])

  useEffect(() => {
    if (!selectedId || !targetVersionId) {
      setCoverage(null)
      setCoverageError('')
      setCoverageLoading(false)
      return
    }
    let active = true
    setCoverage(null)
    setCoverageLoading(true)
    setCoverageError('')
    void evaluationService.getV2DatasetCoverage(selectedId, targetVersionId)
      .then(result => { if (active) setCoverage(result) })
      .catch(cause => {
        if (active) setCoverageError(cause instanceof Error ? cause.message : '读取版本覆盖分布失败')
      })
      .finally(() => { if (active) setCoverageLoading(false) })
    return () => { active = false }
  }, [selectedId, targetVersionId])

  const perform = async (operation: () => Promise<void>) => {
    setBusy(true)
    setError('')
    setMessage('')
    try { await operation() }
    catch (cause) { setError(cause instanceof Error ? cause.message : '操作失败') }
    finally { setBusy(false) }
  }

  const exportVersion = (item: EvaluationV2DatasetVersion) => void perform(async () => {
    setExportingVersionId(item.dataset_version_id)
    try {
      const blob = await evaluationService.exportV2DatasetVersion(item.dataset_id, item.dataset_version_id)
      const downloadUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const datasetPart = (selected?.dataset_key || 'dataset').replace(/[^a-zA-Z0-9._-]/g, '_')
      link.href = downloadUrl
      link.download = `${datasetPart}-${item.version}.jsonl`
      link.hidden = true
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000)
      setMessage(`已导出 Golden ${item.version}，内容哈希已由服务端核验。`)
    } finally {
      setExportingVersionId('')
    }
  })

  const createDataset = () => void perform(async () => {
    const created = await evaluationService.createV2Dataset({
      dataset_key: datasetKey.trim(), name: datasetName.trim(), domain: domain.trim(), description: description.trim(),
    })
    setMessage('已创建 V2 数据集。导入样本后展开“候选审核与修订”完成证据审核，再回到此处发布 Golden。')
    await load()
    setSelectedId(created.dataset_id)
  })

  const manifest = () => ({
    schema_version: selected?.domain === 'rag' ? 'rag-eval/1.0' : 'evaluation-v2/1.0',
    dataset_key: selected?.dataset_key || '',
    name: selected?.name || '',
    version,
    module: selected?.domain || 'rag',
  })

  const inspectFile = () => void perform(async () => {
    if (!selected || !file) throw new Error('先选择 V2 数据集和导入文件')
    const result = await evaluationService.previewV2Import(file, manifest())
    setPreview(result)
    if (!result.valid) throw new Error(`预检发现 ${result.errors.length} 项格式问题；请查看下方行号和字段`)
    setMessage(`预检通过：${result.case_count} 条候选，尚未写入服务端。`)
  })

  const importFile = () => void perform(async () => {
    if (!selected || !file || !preview?.valid) throw new Error('先完成有效的导入预检')
    const result = await evaluationService.importV2Candidates(file, manifest(), createIdempotencyKey())
    setPreview(null)
    setMessage(`已写入 ${result.candidate_count} 条待审核候选，请展开“候选审核与修订”补齐原文证据。`)
    await load()
  })

  const publish = () => void perform(async () => {
    if (!selected) throw new Error('请选择数据集')
    const items = availableCases.filter(item => selectedCases.includes(item.candidate_id)).map(item => ({
      case_id: item.promoted_case_id as string,
      revision: Number(item.payload_json.verified_case_revision || 1),
    }))
    if (!items.length) throw new Error('请至少选择一条已通过 V2 审核的样本')
    const result = await evaluationService.publishV2DatasetVersion(selected.dataset_id, { version, items })
    setMessage(`Golden ${result.version} 已发布，SHA-256 ${result.content_hash}。`)
    setSelectedCases([])
    await load()
    setVersions(await evaluationService.listV2DatasetVersions(selected.dataset_id))
  })

  const createRevisionCandidate = () => void perform(async () => {
    if (!revisionTarget || !revisionQuery.trim()) throw new Error('请选择已审核 Case 并填写评测问题')
    await evaluationService.createV2CaseRevisionCandidate(revisionTargetCaseId, {
      query: revisionQuery,
      observed_answer: revisionObservedAnswer,
      idempotency_key: createIdempotencyKey(),
    })
    setMessage(`Case r${revisionBaseNumber} 的修订候选已进入审核队列；通过后会追加新 revision，旧 Golden 不变。`)
    await load()
    onGoReview?.()
  })

  return <div className="mx-auto max-w-7xl space-y-5 px-6 py-6">
    <section className="rounded-lg border border-border-subtle bg-surface-base p-4" aria-labelledby="v2-dataset-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 id="v2-dataset-heading" className="text-base font-semibold text-text-primary">V2 数据集与不可变 Golden</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">候选必须先审核并核对当前知识库证据。发布时固定样本修订与内容哈希；旧版数据只在下方旧资产区只读展示。</p></div>
        <button type="button" onClick={() => void load()} disabled={loading} className={`${control} inline-flex items-center gap-2`}><RefreshCw size={15} />刷新</button>
      </div>
      {error && <p role="alert" className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
      {message && <p role="status" className="mt-3 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-900">{message}</p>}

      <section className="mt-5 overflow-hidden rounded-md border border-border-subtle" aria-labelledby="v2-dataset-catalog-heading">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border-subtle p-4">
          <div><h3 id="v2-dataset-catalog-heading" className="text-sm font-semibold text-text-primary">V2 数据集目录</h3><p className="mt-1 text-xs text-text-muted">选择一个逻辑数据集以导入候选、发布 Golden 或查看版本。</p></div>
          <label className="text-xs text-text-secondary">搜索数据集<input aria-label="搜索数据集" type="search" value={datasetSearch} onChange={event => setDatasetSearch(event.target.value)} placeholder="名称、标识或业务域" className={`${control} mt-1 min-h-9 w-64`} /></label>
        </div>
        {loading && datasets.length === 0 ? <p className="p-4 text-sm text-text-secondary" role="status">正在读取数据集…</p> : filteredDatasets.length === 0 ? <p className="p-4 text-sm text-text-secondary">{datasets.length ? '没有匹配的数据集。' : '尚无 V2 数据集，可在下方创建。'}</p> : <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead className="bg-bg-elevated text-xs text-text-secondary"><tr><th className="px-4 py-3">名称 / 标识</th><th className="px-4 py-3">业务域</th><th className="px-4 py-3">Golden 版本</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">操作</th></tr></thead><tbody className="divide-y divide-border-subtle">{filteredDatasets.map(item => <tr key={item.dataset_id} className={selectedId === item.dataset_id ? 'bg-accent-soft/40' : ''}><td className="px-4 py-3"><span className="block font-medium text-text-primary">{item.name}</span><span className="font-mono text-xs text-text-muted">{item.dataset_key}</span></td><td className="px-4 py-3">{item.domain}</td><td className="px-4 py-3 tabular-nums">{item.version_count ?? 0}</td><td className="px-4 py-3">{item.status === 'active' ? '启用' : item.status}</td><td className="px-4 py-3"><button type="button" aria-label={`管理数据集 ${item.dataset_key}`} aria-pressed={selectedId === item.dataset_id} onClick={() => setSelectedId(item.dataset_id)} className={`min-h-9 rounded border px-3 text-xs ${selectedId === item.dataset_id ? 'border-accent text-accent' : 'border-border-subtle text-text-secondary hover:bg-bg-hover'}`}>{selectedId === item.dataset_id ? '当前数据集' : '管理此数据集'}</button></td></tr>)}</tbody></table></div>}
      </section>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.5fr)]">
        <form className="space-y-3 rounded-md border border-border-subtle p-4" onSubmit={event => { event.preventDefault(); createDataset() }}>
          <h3 className="text-sm font-semibold text-text-primary">创建逻辑数据集</h3>
          <label className="block text-sm text-text-secondary">数据集标识<input required pattern="[a-z][a-z0-9_-]{2,63}" value={datasetKey} onChange={event => setDatasetKey(event.target.value)} className={`${control} mt-1 w-full`} /></label>
          <label className="block text-sm text-text-secondary">名称<input required maxLength={160} value={datasetName} onChange={event => setDatasetName(event.target.value)} className={`${control} mt-1 w-full`} /></label>
          <label className="block text-sm text-text-secondary">业务域<select required value={domain} onChange={event => setDomain(event.target.value)} className={`${control} mt-1 w-full`}>{domainOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <label className="block text-sm text-text-secondary">说明<textarea maxLength={4000} value={description} onChange={event => setDescription(event.target.value)} className={`${control} mt-1 min-h-20 w-full`} /></label>
          <button type="submit" disabled={busy} className={`${control} w-full bg-accent font-medium text-white`}>创建 V2 数据集</button>
        </form>

        <div className="space-y-4 rounded-md border border-border-subtle p-4">
          <h3 className="text-sm font-semibold text-text-primary">导入并预检候选</h3>
          <p className="text-xs leading-5 text-text-muted">上传所选业务域的 JSON、JSONL 或 CSV。预检不写数据；导入后进入 V2 审核队列，审核通过前不会成为 Golden。</p>
          <ImportTemplateButtons domain={selected?.domain || 'rag'} />
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_150px]">
            <label className="block text-sm text-text-secondary">目标数据集<select value={selectedId} onChange={event => setSelectedId(event.target.value)} className={`${control} mt-1 w-full`}><option value="">选择数据集</option>{datasets.map(item => <option key={item.dataset_id} value={item.dataset_id}>{item.name} · {item.dataset_key}</option>)}</select></label>
            <label className="block text-sm text-text-secondary">版本<input value={version} onChange={event => setVersion(event.target.value)} className={`${control} mt-1 w-full`} /></label>
          </div>
          <label className="block text-sm text-text-secondary">评测文件<input type="file" accept=".json,.jsonl,.csv,application/json,text/csv" onChange={event => { setFile(event.target.files?.[0] || null); setPreview(null) }} className="mt-2 block w-full text-sm text-text-secondary file:mr-3 file:min-h-10 file:rounded-md file:border file:border-border-subtle file:bg-surface-base file:px-3 file:text-text-primary" /></label>
          <div className="flex flex-wrap gap-2"><button type="button" disabled={busy || !file || !selected} onClick={inspectFile} className={control}>{busy ? '处理中…' : '只做预检'}</button><button type="button" disabled={busy || !file || !selected || !preview?.valid} onClick={importFile} className={`${control} bg-accent text-white`}>写入待审核候选</button></div>
          {preview && <div className="rounded-md bg-bg-elevated p-3 text-xs text-text-secondary"><p>{preview.valid ? `预检通过 · ${preview.case_count} 条` : `预检未通过 · ${preview.errors.length} 项错误`}</p>{preview.errors.slice(0, 12).map((item, index) => <p key={`${item.line}-${item.field}-${index}`} className="mt-1 text-red-700">第 {item.line} 行 · {item.field}：{item.message}</p>)}{preview.preview.length > 0 && <pre className="mt-2 max-h-48 overflow-auto rounded bg-surface-base p-2">{JSON.stringify(preview.preview, null, 2)}</pre>}</div>}
          <button type="button" onClick={onGoReview} className="inline-flex min-h-10 items-center gap-1 text-sm font-medium text-accent">打开候选审核 <ArrowRight size={15} /></button>
        </div>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <section className="rounded-md border border-border-subtle p-4" aria-labelledby="v2-approved-cases-heading">
          <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 id="v2-approved-cases-heading" className="text-sm font-semibold text-text-primary">已审核样本</h3><p className="mt-1 text-xs text-text-muted">只显示由当前数据集导入并已 V2 审核通过的候选。</p></div><label className="text-xs text-text-secondary">Golden 版本<input aria-label="Golden 版本" value={version} onChange={event => setVersion(event.target.value)} className={`${control} ml-2 min-h-9 w-28`} /></label></div>
          {!selected ? <p className="mt-4 text-sm text-text-muted">请先选择或创建数据集。</p> : availableCases.length === 0 ? <EmptyState title="暂无可发布样本" description={selected.domain === 'rag' ? '导入候选并在标注审核中补齐证据后，再回来发布。' : '导入候选并完成模块标准结果审核后，再回来发布。'} /> : <div className="mt-3 max-h-72 space-y-2 overflow-auto">{availableCases.map(item => <label key={item.candidate_id} className="flex gap-3 rounded-md border border-border-subtle p-3"><input type="checkbox" checked={selectedCases.includes(item.candidate_id)} onChange={event => setSelectedCases(current => event.target.checked ? [...current, item.candidate_id] : current.filter(id => id !== item.candidate_id))} className="mt-1 size-4 accent-accent" /><span className="min-w-0"><span className="block truncate text-sm text-text-primary">{item.payload_json.input?.query || item.candidate_id}</span><span className="block text-xs text-text-muted">Case {item.promoted_case_id} · 由 {item.created_by} 创建</span></span></label>)}</div>}
          <button type="button" onClick={publish} disabled={busy || !selected || !selectedCases.length} className={`${control} mt-3 bg-accent text-white`}>发布不可变 Golden 版本</button>
          {availableCases.length > 0 && <details className="mt-4 border-t border-border-subtle pt-4">
            <summary className="cursor-pointer text-sm font-medium text-text-primary">修订已审核 Case</summary>
            <p className="mt-2 text-xs leading-5 text-text-muted">修订会以新候选重新审核。原标准事实和原文引用不会自动沿用为有效证据；审核人需要重新确认答案并核验当前证据。</p>
            <label className="mt-3 block text-xs text-text-secondary">目标 Case<select aria-label="目标 Case" value={revisionTargetCaseId} onChange={event => setRevisionTargetCaseId(event.target.value)} className={`${control} mt-1 w-full`}><option value="">选择已审核 Case</option>{availableCases.map(item => <option key={item.promoted_case_id} value={item.promoted_case_id || ''}>r{Number(item.payload_json.verified_case_revision || 1)} · {item.payload_json.input?.query || item.promoted_case_id}</option>)}</select></label>
            {revisionLoading && <p role="status" className="mt-2 text-xs text-text-muted">正在读取最新 Case 修订…</p>}
            {revisionTarget && revisionBaseNumber > 0 && <>
              <p className="mt-2 text-xs text-text-muted">当前基础修订 r{revisionBaseNumber}；审核提交时服务端会再次做版本冲突检查。</p>
              <label className="mt-3 block text-xs text-text-secondary">评测问题<textarea aria-label="修订评测问题" value={revisionQuery} onChange={event => setRevisionQuery(event.target.value)} className={`${control} mt-1 min-h-20 w-full`} /></label>
              <label className="mt-3 block text-xs text-text-secondary">新观察答案（可留空）<textarea aria-label="修订观察答案" value={revisionObservedAnswer} onChange={event => setRevisionObservedAnswer(event.target.value)} className={`${control} mt-1 min-h-20 w-full`} /></label>
              <button type="button" onClick={createRevisionCandidate} disabled={busy || revisionLoading || !revisionQuery.trim()} className={`${control} mt-3 bg-accent text-white`}>提交修订候选并转审核</button>
            </>}
          </details>}
        </section>

        <section className="space-y-4 rounded-md border border-border-subtle p-4" aria-labelledby="v2-version-heading">
          <div><h3 id="v2-version-heading" className="text-sm font-semibold text-text-primary">已发布版本</h3><p className="mt-1 text-xs text-text-muted">版本清单不可变；差异仅展示样本 ID、修订与哈希，不读取答案正文。</p></div>
          {!selected ? <p className="mt-3 text-sm text-text-muted">选择数据集查看版本。</p> : versions.length === 0 ? <p className="mt-3 text-sm text-text-muted">尚无已发布版本。</p> : <div className="mt-3 divide-y divide-border-subtle">{versions.map(item => <article key={item.dataset_version_id} className="py-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm font-medium text-text-primary">v{item.version} · {item.case_count ?? 0} 条</p>
              <div className="flex items-center gap-2">
                <span className="text-xs text-text-secondary">{item.status}</span>
                <button type="button" disabled={busy} onClick={() => exportVersion(item)} aria-label={`导出 Golden ${item.version} JSONL`} className={`${control} inline-flex min-h-9 items-center gap-1.5 px-2.5`}>
                  <Download size={14} />{exportingVersionId === item.dataset_version_id ? '导出中…' : '导出 JSONL'}
                </button>
              </div>
            </div>
            <p className="mt-1 break-all font-mono text-[11px] text-text-muted">SHA-256 {item.content_hash}</p>
            <p className="mt-1 text-xs text-text-muted">发布者 {item.published_by || '—'} · {item.published_at ? new Date(item.published_at).toLocaleString('zh-CN') : '—'}</p>
          </article>)}</div>}
          {versions.length > 0 && <section className="border-t border-border-subtle pt-4" aria-labelledby="v2-coverage-heading">
            <div><h4 id="v2-coverage-heading" className="text-sm font-semibold text-text-primary">版本覆盖分布{coverage ? ` · v${coverage.version}` : ''}</h4><p className="mt-1 text-xs text-text-muted">按已脱敏的类别、场景、风险等级和难度汇总；不读取问题、答案或证据正文。</p></div>
            {coverageLoading && <p role="status" className="mt-3 text-xs text-text-muted">正在统计覆盖分布…</p>}
            {coverageError && <p role="alert" className="mt-3 text-xs text-red-700">{coverageError}</p>}
            {coverage && <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {(Object.keys(coverageLabels) as Array<keyof typeof coverageLabels>).map(field => {
                const dimension = coverage.dimensions[field]
                return <article key={field} className="rounded-md border border-border-subtle p-3">
                  <div className="flex items-baseline justify-between gap-2"><h5 className="text-xs font-semibold text-text-primary">{coverageLabels[field]}</h5><span className="text-xs tabular-nums text-text-secondary">覆盖 {dimension.coverage_ratio}% · {dimension.covered_count}/{coverage.case_count}</span></div>
                  <div className="mt-2 max-h-36 space-y-2 overflow-auto">
                    {dimension.items.map(item => <div key={item.label}>
                      <div className="flex justify-between gap-2 text-[11px] text-text-secondary"><span className="min-w-0 truncate" title={item.label}>{item.label}</span><span className="shrink-0 tabular-nums">{item.count} · {item.ratio}%</span></div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-elevated"><span className="block h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, item.ratio))}%` }} /></div>
                    </div>)}
                    {dimension.items.length === 0 && <p className="text-[11px] text-text-muted">暂无分类数据</p>}
                    {dimension.other_count > 0 && <p className="text-[11px] text-text-muted">另有 {dimension.other_count} 条样本来自低频分类，未逐项展开。</p>}
                  </div>
                </article>
              })}
            </div>}
          </section>}
          {versions.length >= 2 && <div className="border-t border-border-subtle pt-4">
            <h4 className="text-sm font-semibold text-text-primary">版本差异</h4>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-text-secondary">基准版本<select aria-label="基准版本" value={baseVersionId} onChange={event => setBaseVersionId(event.target.value)} className={`${control} mt-1 w-full`}>{versions.map(item => <option key={item.dataset_version_id} value={item.dataset_version_id}>v{item.version}</option>)}</select></label>
              <label className="text-xs text-text-secondary">目标版本<select aria-label="目标版本" value={targetVersionId} onChange={event => setTargetVersionId(event.target.value)} className={`${control} mt-1 w-full`}>{versions.map(item => <option key={item.dataset_version_id} value={item.dataset_version_id}>v{item.version}</option>)}</select></label>
            </div>
            {baseVersionId === targetVersionId && <p className="mt-3 text-xs text-text-muted">请选择两个不同版本。</p>}
            {diffLoading && <p role="status" className="mt-3 text-xs text-text-muted">正在比较版本清单…</p>}
            {diffError && <p role="alert" className="mt-3 text-xs text-red-700">{diffError}</p>}
            {versionDiff && <div className="mt-3 space-y-3">
              <div className="grid grid-cols-2 gap-2 text-center sm:grid-cols-3 xl:grid-cols-6">
                {[[`新增 ${versionDiff.summary.added}`, 'text-green-700'], [`移除 ${versionDiff.summary.removed}`, 'text-red-700'], [`修订变化 ${versionDiff.summary.changed}`, 'text-amber-700'], [`未变化 ${versionDiff.summary.unchanged}`, 'text-text-secondary']].map(([label, tone]) => <p key={label} className={`rounded-md bg-bg-elevated px-2 py-2 text-xs font-medium ${tone}`}>{label}</p>)}
                <p className="rounded-md bg-bg-elevated px-2 py-2 text-xs font-medium text-red-700">基准 stale {versionDiff.stale.base.length}</p>
                <p className="rounded-md bg-bg-elevated px-2 py-2 text-xs font-medium text-red-700">目标 stale {versionDiff.stale.target.length}</p>
              </div>
              {(['added', 'removed', 'changed'] as const).map(kind => {
                const entries = versionDiff[kind]
                if (!entries.length) return null
                return <div key={kind} className="rounded-md border border-border-subtle p-3">
                  <p className="text-xs font-semibold text-text-primary">{kind === 'added' ? '新增样本' : kind === 'removed' ? '移除样本' : '修订变化'}（{entries.length}）</p>
                  <div className="mt-2 max-h-36 space-y-1 overflow-auto font-mono text-[11px] text-text-secondary">
                    {entries.slice(0, 30).map(item => <p key={item.case_id} className="truncate" title={item.case_id}>{item.case_id}{'base_revision' in item ? ` · r${item.base_revision} → r${item.target_revision}` : ` · r${item.revision}`}</p>)}
                  </div>
                  {entries.length > 30 && <p className="mt-2 text-[11px] text-text-muted">仅展示前 30 条；汇总数量包含全部差异。</p>}
                </div>
              })}
              {(versionDiff.stale.base.length > 0 || versionDiff.stale.target.length > 0) && <div className="rounded-md border border-red-200 bg-red-50 p-3">
                <p className="text-xs font-semibold text-red-900">当前证据已无法匹配活跃文档或原文锚点</p>
                <p className="mt-1 text-[11px] text-red-800">stale 仅提示当前证据状态；旧 Golden 保持不可变，修复后需重新审核并发布新版本。</p>
                {([['基准', versionDiff.stale.base], ['目标', versionDiff.stale.target]] as const).filter(([, ids]) => ids.length > 0).map(([label, ids]) => <div key={label} className="mt-2">
                  <p className="text-[11px] font-medium text-red-900">{label}版本 · {ids.length} 条</p>
                  <div className="mt-1 max-h-24 space-y-1 overflow-auto font-mono text-[11px] text-red-800">{ids.slice(0, 30).map(caseId => <p key={caseId} className="truncate" title={caseId}>{caseId}</p>)}</div>
                  {ids.length > 30 && <p className="mt-1 text-[11px] text-red-800">仅展示前 30 条。</p>}
                </div>)}
              </div>}
            </div>}
          </div>}
          {versions.length === 1 && <p className="border-t border-border-subtle pt-3 text-xs text-text-muted">再发布一个 Golden 版本后，可查看样本新增、移除和修订差异。</p>}
        </section>
      </div>
    </section>
  </div>
}
