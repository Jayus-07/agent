'use client'

import { MessageSquare } from 'lucide-react'

const TITLE = 'Multi-Agent 智能助手'
const DESC = 'AI 自动拆解复杂任务，并行调用 📊 SQL 查询、📚 知识库检索 和 📄 报告引擎'

const EXAMPLES = [
  { icon: '📊', label: '数据查询', text: '技术部有多少人？' },
  { icon: '📚', label: '知识检索', text: '微服务架构的最佳实践？' },
  { icon: '📄', label: '生成报告', text: '分析技术部预算使用情况并生成报告' },
  { icon: '🤖', label: '复杂分析', text: '对比各部门绩效，给出改进建议' },
]

interface Props {
  onExampleClick?: (question: string) => void
}

export default function EmptyState({ onExampleClick }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-12">
      <div className="w-14 h-14 rounded-2xl bg-[#2f2f2f] flex items-center justify-center text-[#b4b4b4] mb-5">
        <MessageSquare size={28} />
      </div>
      <h2 className="text-lg font-medium text-[#ececec] mb-1.5">{TITLE}</h2>
      <p className="text-sm text-[#8e8e8e] mb-8 text-center max-w-sm">{DESC}</p>

      <div className="grid gap-2 w-full max-w-md">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.text}
            onClick={() => onExampleClick?.(ex.text)}
            className="text-left px-4 py-3 rounded-xl border border-[#3f3f3f] text-sm text-[#b4b4b4] hover:bg-[#2f2f2f] hover:text-[#ececec] hover:border-[#5f5f5f] transition-all"
          >
            <span className="mr-2">{ex.icon}</span>
            <span className="text-[#8e8e8e] text-xs">{ex.label}</span>
            <br />
            <span>&ldquo;{ex.text}&rdquo;</span>
          </button>
        ))}
      </div>
    </div>
  )
}
