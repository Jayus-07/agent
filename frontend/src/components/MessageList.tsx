'use client'

import type { Message } from '@/lib/types'
import MessageBubble from './MessageBubble'

interface Props { messages: Message[]; isLoading: boolean; sessionId?: string }

/**
 * MessageList 渲染一组消息。
 *
 * 设计要点：
 *   - isLast 显式下沉：让 MessageBubble 用 React.memo 拦截"非最后一条"的重渲染，
 *     流式期间 store 更新不会让历史气泡重渲（P0-4）。
 *   - 每个 assistant 气泡都附带前一条 user 消息（作为 question 字段），用于反馈循环（2026-08-11）。
 */
export default function MessageList({ messages, isLoading: _isLoading, sessionId }: Props) {
  const last = messages.length - 1
  return (
    <div className="max-w-[720px] mx-auto px-5 py-8 space-y-6">
      {messages.map((msg, i) => {
        // 找当前 assistant 消息的上一条 user 消息（作为 question）
        const question = msg.role === 'assistant'
          ? messages.slice(0, i).reverse().find(m => m.role === 'user')?.content
          : undefined
        return (
          <MessageBubble
            key={msg.id}
            message={msg}
            isLast={i === last}
            sessionId={sessionId}
            question={question}
          />
        )
      })}
    </div>
  )
}
