'use client'

import type { Message } from '@/lib/types'
import MessageBubble from './MessageBubble'

interface Props { messages: Message[]; isLoading: boolean }

/**
 * MessageList 渲染一组消息。
 *
 * 设计要点：
 *   - isLast 显式下沉：让 MessageBubble 用 React.memo 拦截"非最后一条"的重渲染，
 *     流式期间 store 更新不会让历史气泡重渲（P0-4）。
 *   - isLoading 在此仅作为 props 透传，未直接使用 —— 留作扩展位。
 */
export default function MessageList({ messages, isLoading: _isLoading }: Props) {
  const last = messages.length - 1
  return (
    <div className="max-w-[720px] mx-auto px-5 py-8 space-y-6">
      {messages.map((msg, i) => (
        <MessageBubble key={msg.id} message={msg} isLast={i === last} />
      ))}
    </div>
  )
}
