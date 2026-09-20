'use client'

/**
 * WelcomeState — 空状态欢迎页（WorkBuddy 工作台式复刻）
 *
 * 结构：大标题「Agent AI，我帮你」+ 能力胶囊行（点击回填输入框发送）。
 * 输入框由 ChatView 紧随其后渲染，胶囊行与输入框视觉上成组（对齐 WorkBuddy：
 * 标题 → 能力胶囊 → 大输入框）。问候语/日期/说明文案让位给 WorkBuddy 式简洁。
 */
import { BookOpen, Database, FileText, LineChart } from 'lucide-react'

const EXAMPLES = [
  { label: '数据查询', text: '查询上个月销量前 10 的商品', Icon: Database },
  { label: '知识检索', text: '退款审核时间是多少？', Icon: BookOpen },
  { label: '生成报告', text: '分析最近一个月价格最高商品并生成报告', Icon: FileText },
  { label: '复杂分析', text: '对比各部门绩效，给出改进建议', Icon: LineChart },
]

interface Props {
  onExampleClick?: (question: string) => void
  budgetBlocked?: boolean
}

export default function EmptyState({ onExampleClick, budgetBlocked = false }: Props) {
  return (
    // 本组件由 ChatView 放进「欢迎区 + 输入框」的居中组内，自身不再承担垂直居中/
    // 撑满高度（外层用 my-auto 整体居中）；my-auto 兼作对话态下超高时的滚动兜底
    <div className="flex flex-col items-center py-2">
      <div className="w-full max-w-3xl my-auto flex flex-col items-center">
        {/* 大标题（WorkBuddy 式：一句口号，不配图标与副文案） */}
        <h1 className="text-[26px] sm:text-[30px] font-bold text-text-primary tracking-tight text-center">
          Agent AI，我帮你
        </h1>

        {/* 能力胶囊行：与下方输入框成组；点击发送完整示例问题，hover 显示全文 */}
        <div className="flex flex-wrap justify-center gap-2 mt-7">
          {EXAMPLES.map(({ label, text, Icon }) => (
            <button
              key={text}
              disabled={budgetBlocked}
              onClick={() => onExampleClick?.(text)}
              title={text}
              aria-label={`示例：${text}`}
              className="flex items-center gap-1.5 rounded-full border border-border-subtle bg-surface-base px-3.5 py-1.5
                text-xs text-text-secondary shadow-[0_1px_2px_rgba(0,0,0,0.03)]
                hover:text-accent hover:border-accent/30 hover:bg-accent/[0.04]
                focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent/40
                transition-all duration-200"
            >
              <Icon size={13} aria-hidden />
              <span>{label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
