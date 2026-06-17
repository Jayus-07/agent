'use client'

import type { Message } from '@/lib/types'
import MessageBubble from './MessageBubble'

interface Props {
  messages: Message[]
  isLoading: boolean
}

export default function MessageList({ messages, isLoading }: Props) {
  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}

      {/* 加载中占位 */}
      {isLoading && (
        <div className="flex items-center gap-1.5 px-1 py-2">
          <span className="typing-dot w-2 h-2 rounded-full bg-[#8e8e8e] inline-block" />
          <span className="typing-dot w-2 h-2 rounded-full bg-[#8e8e8e] inline-block" />
          <span className="typing-dot w-2 h-2 rounded-full bg-[#8e8e8e] inline-block" />
        </div>
      )}
    </div>
  )
}
