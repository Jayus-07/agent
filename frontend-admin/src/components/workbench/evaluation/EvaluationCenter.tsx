'use client'

import WorkbenchShell, { WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DatasetGovernancePanel from './DatasetGovernancePanel'
import EvaluationResultsPanel from './EvaluationResultsPanel'
import FeedbackCandidatesPanel from './FeedbackCandidatesPanel'
import DatasetReviewsPanel from './DatasetReviewsPanel'
import EvaluationBindingsPanel from './EvaluationBindingsPanel'

const TABS = [
  { id: 'datasets', label: '评测集' },
  { id: 'reviews', label: '审核与候选' },
  { id: 'bindings', label: '关联与运行' },
  { id: 'results', label: '评测结果' },
  { id: 'feedback', label: '线上反馈' },
] as const satisfies readonly WorkbenchTab<'results' | 'datasets' | 'feedback' | 'reviews' | 'bindings'>[]

const TAB_IDS = TABS.map((tab) => tab.id) as readonly ('results' | 'datasets' | 'feedback' | 'reviews' | 'bindings')[]

export default function EvaluationCenter() {
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'datasets')

  return (
    <WorkbenchShell
      title="评测中心"
      description="准备评测集 → 核验题目与证据 → 关联知识库 → 开始评测并查看结果。"
      tabs={TABS}
      activeTab={tab}
      onTabChange={selectTab}
    >
      {tab === 'results' && <EvaluationResultsPanel />}
      {tab === 'datasets' && <DatasetGovernancePanel />}
      {tab === 'feedback' && <FeedbackCandidatesPanel />}
      {tab === 'reviews' && <DatasetReviewsPanel />}
      {tab === 'bindings' && <EvaluationBindingsPanel />}
    </WorkbenchShell>
  )
}
