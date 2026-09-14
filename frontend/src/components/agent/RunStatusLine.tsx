'use client'

/**
 * RunStatusLine — 输入框上方的运行状态行
 *
 * P0 数据源：store.isLoading + 当前会话各条消息已固化的 usage.total_tokens 之和。
 * 说明：后端目前只在 done 事件一次性下发本轮用量（SSE 协议 meta|status|log|delta|thinking|done|error），
 * 因此流式进行中无法显示本轮实时累计 —— 这里显示的是「本会话已消耗」。
 * P1 待后端补充实时 usage 后再切换为「本轮已消耗」。
 *
 * 停止生成的入口也放在这一行：状态与操作同处，避免按钮与提示分离。
 */
import { useMemo } from 'react'
import { useChatStore } from '@/store/chat'

interface Props {
  onStop: () => void
}

export default function RunStatusLine({ onStop }: Props) {
  const isLoading = useChatStore((s) => s.isLoading)
  const sessions = useChatStore((s) => s.sessions)
  const currentId = useChatStore((s) => s.currentId)

  const consumed = useMemo(() => {
    const messages = sessions.find((s) => s.id === currentId)?.messages ?? []
    return messages.reduce((sum, m) => sum + (m.usage?.total_tokens ?? 0), 0)
  }, [sessions, currentId])

  if (!isLoading) return null

  return (
    <div className="shrink-0 flex items-center gap-2.5 px-5 pb-2">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
      <span className="text-xs text-text-secondary">生成回复中</span>
      {consumed > 0 && (
        <span className="text-xs text-text-muted tabular-nums">
          · 已消耗 {consumed.toLocaleString()} tokens
        </span>
      )}
      <button
        type="button"
        onClick={onStop}
        className="ml-auto flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-accent/5
          border border-accent/20 text-accent text-xs hover:bg-accent/10 transition-all duration-200"
      >
        <span className="inline-block w-1.5 h-1.5 rounded-sm bg-accent" />
        停止生成
      </button>
    </div>
  )
}
