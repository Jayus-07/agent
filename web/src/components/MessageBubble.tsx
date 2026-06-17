'use client'

import { useMemo } from 'react'
import { User, Bot, Loader2 } from 'lucide-react'
import type { Message } from '@/lib/types'
import { parseProgress } from '@/lib/sse-parser'
import { WORKER_EMOJI } from '@/lib/constants'
import MarkdownContent from './MarkdownContent'
import ThinkingPanel from './ThinkingPanel'

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  const isEmpty = !message.content
  const thinking = message.thinking

  const progress = useMemo(() => {
    if (!thinking?.length) return null
    return parseProgress(thinking)
  }, [thinking])

  return (
    <div className={`animate-fade-in flex gap-4 ${isUser ? 'justify-end' : ''}`}>
      {/* 头像 */}
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-emerald-600 flex items-center justify-center shrink-0">
          <Bot size={18} className="text-white" />
        </div>
      )}

      <div className={`min-w-0 max-w-[85%] ${isUser ? 'order-first' : ''}`}>
        {/* 用户消息 */}
        {isUser ? (
          <div className="bg-[#2f2f2f] rounded-2xl rounded-br-md px-4 py-3 text-[#ececec] text-sm leading-relaxed">
            {message.content}
          </div>
        ) : (
          <div>
            {/* SSE 思考过程 */}
            {thinking && thinking.length > 0 && (
              <ThinkingPanel events={thinking} />
            )}

            {/* 流式传输中：显示进度代替 (无内容) */}
            {isEmpty && progress?.running ? (
              <div className="flex items-center gap-2 text-sm text-[#b4b4b4] py-1">
                <Loader2 size={14} className="text-blue-400 animate-spin shrink-0" />
                <span className="text-[#8e8e8e] shrink-0">
                  {WORKER_EMOJI[progress.running.node] ?? '🔧'}
                </span>
                <span className="truncate text-[#ececec]">{progress.running.description}</span>
                {progress.total > 0 && (
                  <span className="text-xs text-[#8e8e8e] ml-1 shrink-0">
                    [{progress.completed}/{progress.total}]
                  </span>
                )}
                {progress.running.elapsed != null && (
                  <span className="text-xs text-[#6e6e6e] shrink-0 tabular-nums">
                    {progress.running.elapsed.toFixed(1)}s
                  </span>
                )}
              </div>
            ) : isEmpty && !progress ? (
              <div className="flex items-center gap-2 text-sm text-[#b4b4b4] py-1">
                <Loader2 size={14} className="text-blue-400 animate-spin shrink-0" />
                <span className="text-[#ececec]">正在思考...</span>
              </div>
            ) : (
              /* Markdown 正文 */
              <div className="text-sm text-[#ececec]">
                <MarkdownContent content={message.content} />
              </div>
            )}
          </div>
        )}
      </div>

      {/* 用户头像 */}
      {isUser && (
        <div className="w-8 h-8 rounded-full bg-violet-600 flex items-center justify-center shrink-0">
          <User size={16} className="text-white" />
        </div>
      )}
    </div>
  )
}
