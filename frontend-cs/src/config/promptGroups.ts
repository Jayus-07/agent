import type { LucideIcon } from 'lucide-react'
import { Headset, Database, Table, FileText } from 'lucide-react'

export interface PromptGroup {
  id: string
  label: string
  icon: LucideIcon
  keys: string[]
}

export const PROMPT_GROUPS: PromptGroup[] = [
  { id: 'customer_service', label: '客服 Agent', icon: Headset, keys: ['customer_service.system', 'customer_service.answer'] },
  { id: 'rag', label: 'RAG', icon: Database, keys: ['rag.qa'] },
  { id: 'sql', label: 'SQL Agent', icon: Table, keys: ['sql.generator'] },
  { id: 'report', label: 'Report Agent', icon: FileText, keys: ['business_report.polish'] },
]

export const WHITELIST_KEYS = PROMPT_GROUPS.flatMap(g => g.keys)
