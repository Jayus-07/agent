'use client'

import { useEffect, useRef } from 'react'
import { XCircle } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import { useSendMessage } from '@/hooks/useChat'
import MessageList from './MessageList'
import ChatInput from './ChatInput'
import EmptyState from './EmptyState'
import StatusBar from './StatusBar'

export default function ChatView() {
  const messages = useChatStore((s) => s.currentMessages())
  const isLoading = useChatStore((s) => s.isLoading)
  const error = useChatStore((s) => s.error)
  const setError = useChatStore((s) => s.setError)
  const bottomRef = useRef<HTMLDivElement>(null)
  const { send } = useSendMessage()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, messages[messages.length - 1]?.content])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Error banner */}
      {error && (
        <div className="shrink-0 bg-red-900/20 border-b border-red-500/30 px-4 py-2 flex items-center gap-2">
          <XCircle size={14} className="text-red-400 shrink-0" />
          <span className="text-sm text-red-400 flex-1">{error}</span>
          <button
            onClick={() => setError(null)}
            className="text-xs text-red-400/70 hover:text-red-300 shrink-0"
          >
            关闭
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState onExampleClick={send} />
        ) : (
          <MessageList messages={messages} isLoading={isLoading} />
        )}
        <div ref={bottomRef} />
      </div>

      <StatusBar />
      <ChatInput onSend={send} isLoading={isLoading} />
    </div>
  )
}
