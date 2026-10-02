'use client'

import WorkbenchShell, { WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import SelectionDecisionPanel from './SelectionDecisionPanel'
import SelectionFunnelPanel from './SelectionFunnelPanel'

const TABS = [
  { id: 'funnel', label: '选品漏斗' },
  { id: 'decision', label: '选品决策' },
] as const satisfies readonly WorkbenchTab<'funnel' | 'decision'>[]

const TAB_IDS = TABS.map((tab) => tab.id) as readonly ('funnel' | 'decision')[]

export default function SelectionWorkbench() {
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'funnel')

  return (
    <WorkbenchShell
      title="选品工作台"
      description="从数据入池到 Go/No-Go 决策，在同一工作台完成选品闭环。"
      tabs={TABS}
      activeTab={tab}
      onTabChange={selectTab}
    >
      {tab === 'funnel' ? <SelectionFunnelPanel /> : <SelectionDecisionPanel />}
    </WorkbenchShell>
  )
}
