'use client'

/**
 * ProgressCards — 消息流内的 Agent 进度区（生成中）
 *
 * 渲染位置：最后一轮用户提问之下、AI 回答之上（由 MessageList 控制），
 * 仅 isLoading 时显示；流结束后由 MessageBubble 的 TokenInfo/SourceCard 接管结果展示。
 * 包含：Agent 执行时间线（bare 无卡片形态）/ 任务清单 / 产出文件 / token 消耗小字。
 *
 * token 消耗数据源两级 —— usage 事件实时累计优先，本会话已固化 usage 之和兜底。
 * 运行状态与停止入口已上移：输入框右下角的发送键在生成中切换为停止键。
 */

import { useMemo } from 'react'
import AgentTimeline from '@/components/chat/AgentTimeline'
import TodoCard from '@/components/agent/TodoCard'
import FileOpsCard from '@/components/agent/FileOpsCard'
import { useChatStore } from '@/store/chat'

export default function ProgressCards() {
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
    <div className="space-y-2 animate-fade-in">
      {/* 时间线：bare 无卡片形态——未出节点前是一行等待文字，出节点后是缩进列表 */}
      <AgentTimeline collapsed={false} onToggle={() => {}} bare />
      <TodoCard />
      <FileOpsCard />
      {shown > 0 && (
        <div className="text-[10px] text-text-muted tabular-nums">
          已消耗 {shown.toLocaleString()} tokens{liveTotal > 0 ? '（实时）' : ''}
        </div>
      )}
    </div>
  )
}
