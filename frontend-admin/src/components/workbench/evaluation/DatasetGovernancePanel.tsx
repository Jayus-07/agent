'use client'

import { useCallback, useEffect, useState } from 'react'
import { evaluationService, type DatasetCatalogItem, type DatasetVersionDetail, type EvaluationSuite } from '@/api/evaluation'
import DatasetCatalog from '@/components/evaluations/DatasetCatalog'
import DatasetVersionDetailView from '@/components/evaluations/DatasetVersionDetail'
import ImportedDatasetsPanel from './ImportedDatasetsPanel'
import EvaluationV2DatasetPanel from './EvaluationV2DatasetPanel'

export default function DatasetGovernancePanel({ onGoReview }: { onGoReview: () => void }) {
  const [source, setSource] = useState<'business' | 'builtin'>('business')
  const [catalog, setCatalog] = useState<DatasetCatalogItem[]>([])
  const [suites, setSuites] = useState<EvaluationSuite[]>([])
  const [selected, setSelected] = useState<DatasetVersionDetail | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [datasets, suiteItems] = await Promise.all([evaluationService.listDatasets(), evaluationService.listLegacySuites()])
      setCatalog(datasets); setSuites(suiteItems)
    } catch (e) { setError(e instanceof Error ? e.message : '加载内置基准失败') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { if (source === 'builtin') void load() }, [source, load])
  const select = async (item: DatasetCatalogItem) => {
    setSelected(null); setError('')
    try { setSelected(await evaluationService.getDatasetVersion(item.dataset_id, item.dataset_version)) }
    catch (e) { setError(e instanceof Error ? e.message : '读取版本失败') }
  }
  return <div className="mx-auto max-w-7xl space-y-5 px-6 py-6">
    <EvaluationV2DatasetPanel onGoReview={onGoReview} />
    <div className="flex gap-2 border-b border-border-subtle pb-3" role="group" aria-label="评测集来源">{[['business', '业务评测集'], ['builtin', '内置基准']].map(([value, label]) => <button key={value} aria-pressed={source === value} className={`rounded-md px-3 py-2 text-xs ${source === value ? 'bg-accent text-white' : 'bg-slate-100 text-text-secondary'}`} onClick={() => setSource(value as 'business' | 'builtin')}>{label}</button>)}</div>
    {source === 'business' ? <ImportedDatasetsPanel /> : <>
      <p className="text-xs text-text-muted">内置基准随代码发布，覆盖平台能力回归。业务题集在“业务评测集”中导入和发布；审核在“候选审核”中处理。</p>
      <button className="text-xs text-accent" disabled={loading} onClick={() => void load()}>{loading ? '加载中…' : '刷新内置基准'}</button>
      {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
      <div className="grid gap-4 lg:grid-cols-2"><DatasetCatalog items={catalog} selectedId={selected?.dataset_id} onSelect={item => void select(item)} />{selected ? <DatasetVersionDetailView dataset={selected} /> : <p className="p-6 text-xs text-text-muted">选择数据集查看固定版本及覆盖范围。</p>}</div>
      <section className="rounded-lg border border-border-subtle p-4"><h2 className="text-sm font-semibold">内置评测套件</h2><div className="mt-3 divide-y divide-border-subtle">{suites.map(suite => <div className="flex flex-wrap justify-between gap-2 py-3 text-xs" key={`${suite.module}-${suite.name}`}><span>{suite.name} · {suite.case_count} 题</span><span className="text-text-muted">{suite.kb_id} / {suite.fixture_set} / {suite.dataset_version}</span></div>)}</div></section>
    </>}
  </div>
}
