'use client'

import WorkbenchShell, { WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import GatewaySecurityPanel from './GatewaySecurityPanel'
import TokensUsagePanel from './TokensUsagePanel'
import TraceListPanel from './TraceListPanel'
import OverviewPanel from './OverviewPanel'

const TABS = [
  { id: 'overview', label: '运行总览' },
  { id: 'traces', label: '执行记录' },
  { id: 'gateway', label: '网关安全' },
  { id: 'tokens', label: 'Token 用量' },
] as const satisfies readonly WorkbenchTab<'overview' | 'traces' | 'gateway' | 'tokens'>[]

const TAB_IDS = TABS.map((tab) => tab.id) as readonly ('overview' | 'traces' | 'gateway' | 'tokens')[]

export default function MonitoringWorkbench() {
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'overview')

  return (
    <WorkbenchShell
      title="运行控制中心"
      description="从运行状态进入执行记录与 Trace 诊断；所有指标标明时间范围、样本上限与数据来源。"
      tabs={TABS}
      activeTab={tab}
      onTabChange={selectTab}
    >
      {tab === 'overview' && <OverviewPanel />}
      {tab === 'traces' && <TraceListPanel />}
      {tab === 'gateway' && <GatewaySecurityPanel />}
      {tab === 'tokens' && <TokensUsagePanel />}
    </WorkbenchShell>
  )
}
