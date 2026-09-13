'use client'

import { useRef, useEffect } from 'react'
import CSMessageBubble from './CSMessageBubble'
import type { CSMessage } from '@/store/csChat'

interface Props {
  messages: CSMessage[]
  isLoading: boolean
  currentNode?: string | null
}

export default function CSMessageList({ messages, isLoading, currentNode }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, isLoading])

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto px-4 py-4">
      {messages.map((msg, i) => (
        <CSMessageBubble
          key={msg.id}
          message={msg}
          isLast={i === messages.length - 1}
          currentNode={msg.role === 'assistant' ? currentNode : undefined}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
