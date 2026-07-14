'use client'

import type { Message } from '@/lib/types'
import MessageBubble from './MessageBubble'

interface Props { messages: Message[]; isLoading: boolean }

export default function MessageList({ messages, isLoading }: Props) {
  return (
    <div className="max-w-[720px] mx-auto px-5 py-8 space-y-6">
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}
    </div>
  )
}
