'use client'

import { useCallback, useEffect, useState } from 'react'
import { Database, RefreshCw, ShieldCheck } from 'lucide-react'
import { evaluationService, type DatasetCandidate, type DatasetCatalogItem, type DatasetVersionDetail, type EvalRunDetail, type EvaluationSuite } from '@/api/evaluation'
import { useToast } from '@/components/shared/Toast'
import DatasetCatalog from '@/components/evaluations/DatasetCatalog'
import DatasetReviewQueue from '@/components/evaluations/DatasetReviewQueue'
import DatasetVersionDetailView from '@/components/evaluations/DatasetVersionDetail'
import EvaluationRunDetail from '@/components/evaluations/EvaluationRunDetail'

export default function DatasetGovernancePanel() {
  const toast = useToast()
  const [catalog, setCatalog] = useState<DatasetCatalogItem[]>([])
  const [candidates, setCandidates] = useState<DatasetCandidate[]>([])
  const [suites, setSuites] = useState<EvaluationSuite[]>([])
  const [selected, setSelected] = useState<DatasetVersionDetail | null>(null)
  const [latestRun, setLatestRun] = useState<EvalRunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [reviewLoading, setReviewLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [datasets, pending, suiteItems, runs] = await Promise.all([
        evaluationService.listDatasets(),
        evaluationService.listDatasetCandidates('pending_review'),
        evaluationService.listSuites(),
        evaluationService.listRuns(1),
      ])
      setCatalog(datasets)
      setCandidates(pending)
      setSuites(suiteItems)
      if (datasets[0]) {
        const detail = await evaluationService.getDatasetVersion(datasets[0].dataset_id, datasets[0].dataset_version)
        setSelected(detail)
      }
      if (runs[0]) {
        setLatestRun(await evaluationService.getRun(runs[0].run_id))
      } else {
        setLatestRun(null)
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '加载评测集治理数据失败')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { void load() }, [load])

  const handleSelect = async (item: DatasetCatalogItem) => {
    try {
      setSelected(await evaluationService.getDatasetVersion(item.dataset_id, item.dataset_version))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '加载数据集版本失败')
    }
  }

  const handleApprove = async (candidate: DatasetCandidate) => {
    setReviewLoading(true)
    try {
      const approved = await evaluationService.approveDatasetCandidate(candidate.candidate_id, 'admin-console')
      toast.success(`候选已审核，生成不可变版本 ${approved.approved_version}`)
      await load()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '审核候选失败')
    } finally {
      setReviewLoading(false)
    }
  }

  const handleReject = async (candidate: DatasetCandidate) => {
    const reason = window.prompt('请输入驳回原因', '标注不足或不符合当前 Suite')
    if (reason === null) return
    setReviewLoading(true)
    try {
      await evaluationService.rejectDatasetCandidate(candidate.candidate_id, 'admin-console', reason)
      toast.info('候选已驳回并记录审核原因')
      await load()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '驳回候选失败')
    } finally {
      setReviewLoading(false)
    }
  }

  return (
      <div className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-end gap-3">
          <button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-1.5 rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary disabled:opacity-50"><RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> 刷新治理状态</button>
        </div>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.9fr)]">
          <DatasetCatalog items={catalog} selectedId={selected?.dataset_id} onSelect={handleSelect} />
          {selected ? <DatasetVersionDetailView dataset={selected} /> : <div className="rounded-xl border border-border-subtle bg-surface-base px-4 py-8 text-center text-xs text-text-muted">选择一个数据集查看版本详情</div>}
        </div>

        <div className="mt-4"><DatasetReviewQueue candidates={candidates} loading={reviewLoading} onApprove={handleApprove} onReject={handleReject} /></div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <section className="rounded-xl border border-border-subtle bg-surface-base p-4" aria-label="评测 Suite">
            <div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold text-text-primary">Suite 与触发范围</h2><p className="mt-0.5 text-[11px] text-text-muted">发布门禁只引用声明过的 Suite，不允许临时拼接 case</p></div><ShieldCheck size={15} className="text-emerald-600" /></div>
            <div className="mt-3 space-y-2">{suites.slice(0, 8).map(suite => <div key={`${suite.module}-${suite.name}`} className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2"><div className="min-w-0"><div className="text-xs font-medium text-text-primary">{suite.name}</div><div className="mt-0.5 text-[10px] text-text-muted">{suite.module} · {suite.case_count} cases · {suite.kb_id} / {suite.fixture_set}</div></div><span className="shrink-0 font-mono text-[10px] text-text-muted">{suite.dataset_version}</span></div>)}{suites.length === 0 && <p className="py-4 text-center text-xs text-text-muted">暂无 Suite</p>}</div>
          </section>
          <EvaluationRunDetail run={latestRun} />
        </div>
      </div>
  )
}
