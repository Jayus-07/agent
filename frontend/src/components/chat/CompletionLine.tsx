'use client'

/**
 * CompletionLine — AI 回复完成态常驻行（WorkBuddy 式）
 *
 * 形态：「✓ Agent · 已完成 · 12.3s ›」，点击展开回看执行过程
 * （AgentTimeline + 任务清单，数据为 done 时固化进 message.trace 的快照）。
 * 历史恢复的消息无 trace，不渲染本行。
 * trace.streamEvents 为空（direct 快路径无节点事件）时只展示耗时，不渲染时间线。
 */

import { useState } from 'react'
import { CheckCircle2, ChevronDown } from 'lucide-react'
import type { AgentTrace } from '@/lib/types'
import AgentTimeline from './AgentTimeline'
import TodoCard from '@/components/agent/TodoCard'

export default function CompletionLine({ trace }: { trace: AgentTrace }) {
  const [open, setOpen] = useState(false)
  const hasTimeline = trace.streamEvents.length > 0
  const hasTodos = trace.todoItems.length > 0

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1.5 py-0.5 text-xs text-text-muted hover:text-text-secondary transition-colors"
      >
        <CheckCircle2 size={13} className="text-green-500" />
        <span className="font-medium text-text-secondary">Agent</span>
        <span>已完成{trace.elapsed > 0 ? ` · ${trace.elapsed.toFixed(1)}s` : ''}</span>
        {(hasTimeline || hasTodos) && (
          <ChevronDown
            size={13}
            className={`transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          />
        )}
      </button>

      {open && (hasTimeline || hasTodos) && (
        <div className="mt-2 space-y-3">
          {hasTimeline && (
            <AgentTimeline collapsed={false} onToggle={() => {}} events={trace.streamEvents} nodeLabels={trace.nodeLabels} />
          )}
          {hasTodos && <TodoCard items={trace.todoItems} />}
        </div>
      )}
    </div>
  )
}
