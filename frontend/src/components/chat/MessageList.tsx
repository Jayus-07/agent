'use client'

import { Fragment } from 'react'
import type { Message } from '@/lib/types'
import MessageBubble from './MessageBubble'
import ProgressCards from './ProgressCards'

interface Props { messages: Message[]; isLoading: boolean; sessionId?: string; onStop?: () => void }

/**
 * MessageList 渲染一组消息。
 *
 * 设计要点：
 *   - isLast 显式下沉：让 MessageBubble 用 React.memo 拦截"非最后一条"的重渲染，
 *     流式期间 store 更新不会让历史气泡重渲（P0-4）。
 *   - 每个 assistant 气泡都附带前一条 user 消息（作为 question 字段），用于反馈循环（2026-08-11）。
 *   - WorkBuddy 式进度卡片：isLoading 时嵌入最后一轮 user 消息之下、回答之上
 *     （AgentTimeline / TodoCard / FileOpsCard），流结束自动消失。
 */
export default function MessageList({ messages, isLoading, sessionId, onStop }: Props) {
  const last = messages.length - 1
  // 最后一轮 user 提问的位置：进度卡片插在其后
  let lastUserIdx = -1
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'user') { lastUserIdx = i; break }
  }

  return (
    <div className="max-w-4xl mx-auto px-5 py-8 space-y-6">
      {messages.map((msg, i) => {
        // 找当前 assistant 消息的上一条 user 消息（作为 question）
        const question = msg.role === 'assistant'
          ? messages.slice(0, i).reverse().find(m => m.role === 'user')?.content
          : undefined
        return (
          <Fragment key={msg.id}>
            <MessageBubble
              message={msg}
              isLast={i === last}
              sessionId={sessionId}
              question={question}
            />
            {isLoading && i === lastUserIdx && <ProgressCards onStop={onStop ?? (() => {})} />}
          </Fragment>
        )
      })}
    </div>
  )
}
