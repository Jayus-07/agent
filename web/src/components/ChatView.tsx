'use client'

import { useEffect, useRef } from 'react'
import { useChatStore } from '@/store/chat'
import { useSendMessage } from '@/hooks/useChat'
import MessageList from './MessageList'
import ChatInput from './ChatInput'
import EmptyState from './EmptyState'

export default function ChatView() {
  const messages = useChatStore((s) => s.currentMessages())
  const isLoading = useChatStore((s) => s.isLoading)
  const bottomRef = useRef<HTMLDivElement>(null)
  const { send } = useSendMessage()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, messages[messages.length - 1]?.content])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState onExampleClick={send} />
        ) : (
          <MessageList messages={messages} isLoading={isLoading} />
        )}
        <div ref={bottomRef} />
      </div>

      <ChatInput onSend={send} isLoading={isLoading} />
    </div>
  )
}
