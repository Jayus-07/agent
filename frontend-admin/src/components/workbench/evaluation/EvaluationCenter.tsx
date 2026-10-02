'use client'

import WorkbenchShell, { WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DatasetGovernancePanel from './DatasetGovernancePanel'
import EvaluationResultsPanel from './EvaluationResultsPanel'
import FeedbackCandidatesPanel from './FeedbackCandidatesPanel'

const TABS = [
  { id: 'results', label: '评测结果' },
  { id: 'datasets', label: '评测集治理' },
  { id: 'feedback', label: '反馈候选' },
] as const satisfies readonly WorkbenchTab<'results' | 'datasets' | 'feedback'>[]

const TAB_IDS = TABS.map((tab) => tab.id) as readonly ('results' | 'datasets' | 'feedback')[]

export default function EvaluationCenter() {
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'results')

  return (
    <WorkbenchShell
      title="评测中心"
      description="统一查看评测结果、治理评测集并审核反馈候选。"
      tabs={TABS}
      activeTab={tab}
      onTabChange={selectTab}
    >
      {tab === 'results' && <EvaluationResultsPanel />}
      {tab === 'datasets' && <DatasetGovernancePanel />}
      {tab === 'feedback' && <FeedbackCandidatesPanel />}
    </WorkbenchShell>
  )
}
