'use client'

/**
 * 预算治理页（2026-11-10 Tab 化重构）：单页 8 个区块滚动过长，改为
 * WorkbenchShell 四标签（与运行控制中心同款 ?tab= 深链）：
 * - 概览：汇总卡 + 月度对账
 * - 待对账：队列摘要 + 展开明细/批量核销
 * - 主体与策略：预算主体（搜索/测试数据开关/分页）+ 策略版本
 * - 审计：阈值事件 + 策略变更审计
 * 各面板逻辑见同目录 panels/*；共享部件见 shared.tsx。
 */
import { atLeast } from '@/lib/auth'
import WorkbenchShell, { type WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import RoleGate from '@/components/auth/RoleGate'

import BudgetOverviewPanel from './panels/BudgetOverviewPanel'
import BudgetReconciliationPanel from './panels/BudgetReconciliationPanel'
import BudgetSubjectsPanel from './panels/BudgetSubjectsPanel'
import BudgetAuditPanel from './panels/BudgetAuditPanel'

const TABS = [
  { id: 'overview', label: '概览' },
  { id: 'reconciliation', label: '待对账' },
  { id: 'subjects', label: '主体与策略' },
  { id: 'audit', label: '审计' },
] as const satisfies readonly WorkbenchTab<'overview' | 'reconciliation' | 'subjects' | 'audit'>[]

const TAB_IDS = TABS.map((tab) => tab.id) as readonly ('overview' | 'reconciliation' | 'subjects' | 'audit')[]

function ReadOnlyNotice() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      <div className="rounded-xl border border-blue-100 bg-blue-50 p-4 text-xs text-blue-800">
        当前角色为只读模式。管理员策略、主体和审计明细需要管理员权限。
      </div>
    </div>
  )
}

export default function BudgetGovernancePage() {
  const canAdmin = atLeast('admin')
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'overview')

  return (
    <RoleGate minRole="viewer" pageName="预算治理">
      <WorkbenchShell
        title="预算治理"
        description="预算状态由后端权威计算；金额为记账本位币人民币（CNY），美元报价按显式汇率折算。"
        tabs={TABS}
        activeTab={tab}
        onTabChange={selectTab}
      >
        {tab === 'overview' && <BudgetOverviewPanel canAdmin={canAdmin} />}
        {tab === 'reconciliation' && (canAdmin ? <BudgetReconciliationPanel /> : <ReadOnlyNotice />)}
        {tab === 'subjects' && (canAdmin ? <BudgetSubjectsPanel /> : <ReadOnlyNotice />)}
        {tab === 'audit' && (canAdmin ? <BudgetAuditPanel /> : <ReadOnlyNotice />)}
      </WorkbenchShell>
    </RoleGate>
  )
}
