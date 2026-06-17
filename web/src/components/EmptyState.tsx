'use client'

import { MessageSquare, Database, Library, FileText } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import type { ChatMode } from '@/lib/types'

const configs: Record<ChatMode, { icon: React.ReactNode; title: string; desc: string; examples: string[] }> = {
  chat: {
    icon: <MessageSquare size={28} />,
    title: 'Multi-Agent 对话',
    desc: 'AI 自动拆解复杂任务，并行调用 SQL、知识库和报告引擎',
    examples: ['技术部有多少人？', '分析技术部预算使用情况，并生成报告'],
  },
  sql: {
    icon: <Database size={28} />,
    title: 'SQL 安全查询',
    desc: '自然语言转 SQL，6 层安全校验 + 行级过滤',
    examples: ['查询所有项目预算', '技术部有哪些员工'],
  },
  rag: {
    icon: <Library size={28} />,
    title: '知识库检索',
    desc: '基于 RAG 从文档中检索相关信息并生成回答',
    examples: ['张三是谁？', '微服务架构的最佳实践'],
  },
  report: {
    icon: <FileText size={28} />,
    title: '报告生成',
    desc: '基于数据库实时数据，自动生成 Markdown 报告（含图表）',
    examples: ['月销售报告', '部门预算分析'],
  },
}

interface Props {
  onExampleClick?: (question: string) => void
}

export default function EmptyState({ onExampleClick }: Props) {
  const mode = useChatStore((s) => s.mode)
  const cfg = configs[mode]

  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-12">
      <div className="w-14 h-14 rounded-2xl bg-[#2f2f2f] flex items-center justify-center text-[#b4b4b4] mb-5">
        {cfg.icon}
      </div>
      <h2 className="text-lg font-medium text-[#ececec] mb-1.5">{cfg.title}</h2>
      <p className="text-sm text-[#8e8e8e] mb-8 text-center max-w-sm">{cfg.desc}</p>

      <div className="grid gap-2 w-full max-w-md">
        {cfg.examples.map((q) => (
          <button
            key={q}
            onClick={() => onExampleClick?.(q)}
            className="text-left px-4 py-3 rounded-xl border border-[#3f3f3f] text-sm text-[#b4b4b4] hover:bg-[#2f2f2f] hover:text-[#ececec] hover:border-[#5f5f5f] transition-all"
          >
            &ldquo;{q}&rdquo;
          </button>
        ))}
      </div>
    </div>
  )
}
