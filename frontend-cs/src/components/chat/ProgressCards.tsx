'use client'

/**
 * ProgressCards — 消息流内的 Agent 进度卡片组（WorkBuddy 式）
 *
 * 渲染位置：最后一轮用户提问之下、AI 回答之上（由 MessageList 控制），
 * 仅 isLoading 时显示；流结束后由 MessageBubble 的 TokenInfo/SourceCard 接管结果展示。
 * 包含：Agent 执行时间线（可折叠）/ 任务清单 / 产出文件 / 运行状态行。
 *
 * 运行状态行对齐 WorkBuddy「生成回复中 · 已消耗 3.67」流内形态：
 * 数据源两级 —— usage 事件实时累计优先，本会话已固化 usage 之和兜底。
 */

import { useMemo, useState } from 'react'
import { Square } from 'lucide-react'
import AgentTimeline from '@/components/chat/AgentTimeline'
import TodoCard from '@/components/agent/TodoCard'
import FileOpsCard from '@/components/agent/FileOpsCard'
import { useChatStore } from '@/store/chat'

export default function ProgressCards({ onStop }: { onStop: () => void }) {
  const [tlOpen, setTlOpen] = useState(true)
  const streamUsage = useChatStore((s) => s.streamUsage)
  const sessions = useChatStore((s) => s.sessions)
  const currentId = useChatStore((s) => s.currentId)

  const consumed = useMemo(() => {
    const messages = sessions.find((s) => s.id === currentId)?.messages ?? []
    return messages.reduce((sum, m) => sum + (m.usage?.total_tokens ?? 0), 0)
  }, [sessions, currentId])

  // 实时值优先：本轮已有 usage 事件则显示实时累计，否则兜底会话累计
  const liveTotal = streamUsage?.total_tokens ?? 0
  const shown = liveTotal > 0 ? liveTotal : consumed

  return (
    <div className="space-y-3 animate-fade-in">
      <AgentTimeline collapsed={!tlOpen} onToggle={() => setTlOpen((v) => !v)} />
      <TodoCard />
      <FileOpsCard />

      {/* 运行状态行（流内，WorkBuddy 式） */}
      <div className="flex items-center gap-2 pt-0.5">
        <span className="flex items-center gap-1.5 text-xs text-text-secondary">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          生成回复中
        </span>
        {shown > 0 && (
          <span className="text-xs text-text-muted tabular-nums">
            · 已消耗 {shown.toLocaleString()} tokens{liveTotal > 0 ? '（实时）' : ''}
          </span>
        )}
        <button
          type="button"
          onClick={onStop}
          className="ml-auto flex items-center gap-1.5 px-3 py-1 rounded-full bg-accent/5
            border border-accent/20 text-accent text-xs hover:bg-accent/10 transition-all duration-200"
          title="停止生成"
        >
          <Square size={9} className="fill-current" />
          停止生成
        </button>
      </div>
    </div>
  )
}
