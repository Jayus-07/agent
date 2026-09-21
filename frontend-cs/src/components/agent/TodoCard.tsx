'use client'

/**
 * TodoCard — 任务清单卡片（P1：todo 事件渲染）
 *
 * 数据源：store.todoItems —— 后端在 planner / critique / supervisor 节点
 * 跑完后下发的全量快照（supervisor 轮次会随步骤完成持续刷新状态）。
 * 全量替换语义：前端不做增量合并，直接渲染最新一份。
 * 无任务时不渲染；与 AgentTimeline 同区（执行进度信息带）。
 */

import type { ReactNode } from 'react'
import {
  AlertCircle, CheckCircle2, Circle, Clock, ListTodo, MinusCircle,
} from 'lucide-react'
import { useChatStore } from '@/store/chat'
import type { TodoItem } from '@/lib/types'

const STATUS_ICON: Record<TodoItem['status'], ReactNode> = {
  pending: <Circle size={13} className="text-text-muted" />,
  in_progress: <Clock size={13} className="text-amber-500 animate-pulse" />,
  completed: <CheckCircle2 size={13} className="text-green-500" />,
  failed: <AlertCircle size={13} className="text-red-500" />,
  skipped: <MinusCircle size={13} className="text-text-muted" />,
}

const STATUS_LABEL: Record<TodoItem['status'], string> = {
  pending: '待执行',
  in_progress: '执行中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
}

export default function TodoCard({ items: itemsProp }: { items?: TodoItem[] }) {
  const storeItems = useChatStore((s) => s.todoItems)
  // 传入固化快照时以 props 为准（完成态回看）
  const items = itemsProp ?? storeItems
  if (items.length === 0) return null

  const done = items.filter((i) => i.status === 'completed' || i.status === 'skipped').length

  return (
    <div className="border border-border-subtle rounded-xl bg-surface-elevated overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle">
        <div className="flex items-center gap-2 text-xs font-medium text-text-primary">
          <ListTodo size={13} className="text-accent" />
          任务清单
        </div>
        <span className="text-[10px] text-text-muted tabular-nums">{done}/{items.length} 完成</span>
      </div>
      <div className="px-4 py-2" role="list" aria-label="Agent 任务清单">
        {items.map((item) => (
          <div key={item.id} role="listitem" className="flex items-start gap-2 py-0.5"
            title={STATUS_LABEL[item.status]}>
            <span className="shrink-0 mt-0.5" aria-hidden>{STATUS_ICON[item.status]}</span>
            <span className={`text-[11px] leading-relaxed break-words ${
              item.status === 'completed' ? 'text-text-muted line-through'
              : item.status === 'failed' ? 'text-red-500'
              : 'text-text-secondary'
            }`}>
              {item.text}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
