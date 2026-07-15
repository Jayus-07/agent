'use client'

import { Sparkles } from 'lucide-react'

const EXAMPLES = [
  { label: '数据查询', text: '？' },
  { label: '知识检索', text: '退款审核时间是多少？' },
  { label: '生成报告', text: '分析最近一个月价格最高商品并生成报告' },
  { label: '复杂分析', text: '对比各部门绩效，给出改进建议' },
]

interface Props { onExampleClick?: (question: string) => void }

export default function EmptyState({ onExampleClick }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-16">
      {/* Logo */}
      <div className="w-16 h-16 rounded-2xl bg-accent/8 flex items-center justify-center mb-8">
        <Sparkles size={30} className="text-accent" strokeWidth={1.5} />
      </div>

      <h1 className="text-2xl font-semibold text-text-primary mb-3 tracking-tight">
        有什么可以帮助你的？
      </h1>
      <p className="text-sm text-text-muted mb-10 text-center max-w-md leading-relaxed">
        AI 自动拆解复杂任务，并行调用数据查询、知识检索和报告引擎
      </p>

      {/* 示例卡片 */}
      <div className="grid gap-2.5 w-full max-w-lg">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.text}
            onClick={() => onExampleClick?.(ex.text)}
            className="group text-left px-5 py-3.5 rounded-xl bg-surface-base border border-border-subtle
              text-sm text-text-secondary hover:text-text-primary hover:border-accent/30 hover:shadow-card
              hover:-translate-y-px transition-all duration-200"
          >
            <span className="inline-block text-accent text-xs font-medium mb-1 bg-accent/5 px-2 py-0.5 rounded-full">
              {ex.label}
            </span>
            <br />
            <span className="text-text-primary">&ldquo;{ex.text}&rdquo;</span>
          </button>
        ))}
      </div>
    </div>
  )
}
