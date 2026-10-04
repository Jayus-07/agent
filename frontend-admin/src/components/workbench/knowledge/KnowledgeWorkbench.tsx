'use client'

import RoleGate from '@/components/auth/RoleGate'
import WorkbenchShell, { WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DocumentsPanel from './DocumentsPanel'
import KeywordsPanel from './KeywordsPanel'
import OperationsPanel from './OperationsPanel'
import KbAuthorityPanel from './KbAuthorityPanel'
import PendingReviewPanel from './PendingReviewPanel'
import UploadFailuresPanel from './UploadFailuresPanel'

const TABS = [
  { id: 'documents', label: '文档' },
  { id: 'pending', label: '待复核' },
  { id: 'failures', label: '入库失败' },
  { id: 'keywords', label: '词库' },
  { id: 'operations', label: '操作日志' },
  { id: 'authority', label: '授权盘点' },
] as const satisfies readonly WorkbenchTab<'documents' | 'pending' | 'failures' | 'keywords' | 'operations' | 'authority'>[]

const TAB_IDS = TABS.map((tab) => tab.id) as readonly ('documents' | 'pending' | 'failures' | 'keywords' | 'operations' | 'authority')[]

export default function KnowledgeWorkbench() {
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'documents')

  return (
    <RoleGate minRole="editor" pageName="知识库工作台">
      <WorkbenchShell
        title="知识库工作台"
        description="统一处理文档入库、审核、词库治理和操作追踪。"
        tabs={TABS}
        activeTab={tab}
        onTabChange={selectTab}
      >
        {tab === 'documents' && <DocumentsPanel />}
        {tab === 'pending' && <PendingReviewPanel />}
        {tab === 'failures' && <UploadFailuresPanel />}
        {tab === 'keywords' && <KeywordsPanel />}
        {tab === 'operations' && <OperationsPanel />}
        {tab === 'authority' && <KbAuthorityPanel />}
      </WorkbenchShell>
    </RoleGate>
  )
}
