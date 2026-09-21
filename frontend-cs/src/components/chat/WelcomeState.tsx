'use client'

import { Sparkles } from 'lucide-react'

const EXAMPLES = [
  { label: '数据查询', text: '查询上个月销量前 10 的商品' },
  { label: '知识检索', text: '退款审核时间是多少？' },
  { label: '生成报告', text: '分析最近一个月价格最高商品并生成报告' },
  { label: '复杂分析', text: '对比各部门绩效，给出改进建议' },
]

interface Props {
  onExampleClick?: (question: string) => void
  budgetBlocked?: boolean
}

export default function EmptyState({ onExampleClick, budgetBlocked = false }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-16">
      <div className="w-14 h-14 rounded-2xl bg-accent/8 flex items-center justify-center mb-6">
        <Sparkles size={30} className="text-accent" strokeWidth={1.5} />
      </div>

      <h1 className="text-xl font-semibold text-text-primary mb-2.5 tracking-tight">
        描述你要完成的任务
      </h1>
      <p className="text-[13px] text-text-muted mb-8 text-center max-w-md leading-relaxed">
        AI 自动拆解任务，并行调用数据查询、知识检索与报告引擎，产出可直接使用的结论
      </p>

      <div className="grid gap-2.5 w-full max-w-2xl">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.text}
            disabled={budgetBlocked}
            onClick={() => onExampleClick?.(ex.text)}
            className="group text-left px-5 py-3 rounded-xl bg-surface-base border border-border-subtle
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
