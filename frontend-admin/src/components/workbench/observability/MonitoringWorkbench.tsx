'use client'

import WorkbenchShell, { WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import GatewayPanel from './GatewayPanel'
import TokensPanel from './TokensPanel'
import TracesPanel from './TracesPanel'

const TABS = [
  { id: 'traces', label: '问答追踪' },
  { id: 'gateway', label: '网关安全' },
  { id: 'tokens', label: 'Token 用量' },
] as const satisfies readonly WorkbenchTab<'traces' | 'gateway' | 'tokens'>[]

const TAB_IDS = TABS.map((tab) => tab.id) as readonly ('traces' | 'gateway' | 'tokens')[]

export default function MonitoringWorkbench() {
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'traces')

  return (
    <WorkbenchShell
      title="运行监控工作台"
      description="统一查看问答链路、网关安全事件和 Token/成本用量。"
      tabs={TABS}
      activeTab={tab}
      onTabChange={selectTab}
    >
      {tab === 'traces' && <TracesPanel />}
      {tab === 'gateway' && <GatewayPanel />}
      {tab === 'tokens' && <TokensPanel />}
    </WorkbenchShell>
  )
}
