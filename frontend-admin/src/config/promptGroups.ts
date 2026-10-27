import type { LucideIcon } from 'lucide-react'
import { Headset, Database, Table, FileText, Wrench } from 'lucide-react'

export interface PromptGroup {
  id: string
  label: string
  icon: LucideIcon
  keys: string[]
}

export const PROMPT_GROUPS: PromptGroup[] = [
  {
    id: 'customer_service',
    label: '客服 Agent',
    icon: Headset,
    keys: [
      'customer_service.system',
      'customer_service.answer',
      'customer_service.query_intent',
      'customer_service.complaint_assess',
      'customer_service.supervisor',
      'customer_service.redirect_main',
    ],
  },
  { id: 'rag', label: 'RAG', icon: Database, keys: ['rag.qa'] },
  { id: 'sql', label: 'SQL Agent', icon: Table, keys: ['sql.generator'] },
  { id: 'report', label: 'Report Agent', icon: FileText, keys: ['business_report.polish'] },
  {
    id: 'tool_governance',
    label: '工具治理（发现与选择）',
    icon: Wrench,
    keys: [
      'router.tool_selector',
    ],
  },
]

export const WHITELIST_KEYS = PROMPT_GROUPS.flatMap(g => g.keys)
