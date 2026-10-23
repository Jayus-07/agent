'use client'

import { useCallback, useEffect, useState } from 'react'
import ImportedDatasetsPanel from './ImportedDatasetsPanel'
import DatasetReviewQueue from '@/components/evaluations/DatasetReviewQueue'
import CandidateToDatasetPanel from './CandidateToDatasetPanel'
import { evaluationService, type DatasetCandidate } from '@/api/evaluation'
import { useToast } from '@/components/shared/Toast'

export default function DatasetReviewsPanel() {
  const toast = useToast()
  const [items, setItems] = useState<DatasetCandidate[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    try { setItems(await evaluationService.listDatasetCandidates('pending_review')); setError('') }
    catch (e) { setError(e instanceof Error ? e.message : '加载候选失败') }
  }, [])
  useEffect(() => { void load() }, [load])
  const review = async (item: DatasetCandidate, approve: boolean) => {
    const reason = window.prompt('填写审核依据或驳回原因')
    if (!reason?.trim()) return
    setBusy(true)
    try {
      if (approve) await evaluationService.approveDatasetCandidate(item.candidate_id, 'current-user')
      else await evaluationService.rejectDatasetCandidate(item.candidate_id, 'current-user', reason)
      toast.success('候选审核已保存'); await load()
    } catch (e) { setError(e instanceof Error ? e.message : '审核失败') }
    finally { setBusy(false) }
  }
  return <div className="mx-auto max-w-7xl space-y-6 px-6 py-6"><ImportedDatasetsPanel review /><CandidateToDatasetPanel items={items} />{error && <p role="alert" className="text-xs text-red-700">{error}</p>}<div><h2 className="mb-3 text-sm font-semibold">Trace 与反馈生成的用例候选</h2><DatasetReviewQueue candidates={items} loading={busy} onApprove={item => void review(item, true)} onReject={item => void review(item, false)} /></div></div>
}
