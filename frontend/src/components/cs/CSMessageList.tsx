'use client'

import { useRef, useEffect } from 'react'
import CSMessageBubble from './CSMessageBubble'
import type { CSMessage } from '@/store/csChat'
import CSTimeline from './CSTimeline'

interface Props {
  messages: CSMessage[]
  isLoading: boolean
  currentNode?: string | null
  timeline?: string[]
}

export default function CSMessageList({ messages, isLoading, currentNode, timeline = [] }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, isLoading, timeline.length])

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
      {isLoading && timeline.length > 0 && (
        <div className="ml-11 -mt-2 mb-4" data-testid="cs-live-progress">
          <p className="text-[10px] text-text-muted mb-1">本轮处理进度</p>
          <CSTimeline nodes={timeline} currentNode={currentNode} />
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
