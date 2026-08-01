'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { useSearchParams } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import { useSendMessage } from '@/hooks/useChat'
import MessageList from './MessageList'
import ChatInput from './ChatInput'
import EmptyState from './EmptyState'
import StatusBar from './StatusBar'
import LLMSwitcher from './LLMSwitcher'
import MultiQueryToggle from './MultiQueryToggle'
import ContextPanel from './chat/ContextPanel'
import AgentTimeline from './chat/AgentTimeline'

export default function ChatView() {
  const messages = useChatStore((s) => s.currentMessages())
  const isLoading = useChatStore((s) => s.isLoading)
  const error = useChatStore((s) => s.error)
  const setError = useChatStore((s) => s.setError)
  const currentId = useChatStore((s) => s.currentId)
  const [timelineOpen, setTimelineOpen] = useState(true)
  const { send, stopStream } = useSendMessage()

  const bottomRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const [userScrolling, setUserScrolling] = useState(false)
  const scrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleScroll = useCallback(() => {
    const el = contentRef.current; if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (!atBottom) {
      setUserScrolling(true)
      if (scrollTimer.current) clearTimeout(scrollTimer.current)
      scrollTimer.current = setTimeout(() => setUserScrolling(false), 4000)
    } else {
      setUserScrolling(false)
    }
  }, [])

  const searchParams = useSearchParams()
  const loadHistory = useChatStore((s) => s.loadHistory)
  const switchSession = useChatStore((s) => s.switchSession)

  useEffect(() => {
    if (!userScrolling) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, messages[messages.length - 1]?.content, userScrolling])

  // 加载历史会话
  useEffect(() => {
    const sessionParam = searchParams.get('session')
    if (sessionParam) {
      switchSession(sessionParam)
      loadHistory(sessionParam)
    }
  }, [searchParams])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Error toast */}
      {error && (
        <div className="shrink-0 mx-5 mt-3 bg-red-50 border border-red-200 rounded-xl px-4 py-3 flex items-center gap-2.5 animate-fade-in">
          <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />
          <span className="text-sm text-red-700 flex-1">{error}</span>
          <button onClick={() => setError(null)} className="text-xs text-red-400 hover:text-red-600 shrink-0 transition-colors">
            关闭
          </button>
        </div>
      )}

      {/* Memory context panel */}
      <ContextPanel sessionId={currentId} />

      {/* Agent Timeline — 已关闭：对话页不显示思维链中间步骤 */}

      {/* Messages */}
      <div ref={contentRef} onScroll={handleScroll} className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState onExampleClick={send} />
        ) : (
          <MessageList messages={messages} isLoading={isLoading} />
        )}
        <div ref={bottomRef} />
      </div>

      {/* Status */}
      <StatusBar />

      {/* Stop button */}
      {isLoading && (
        <div className="shrink-0 flex justify-center pb-2">
          <button onClick={stopStream}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-accent/5 border border-accent/20
              text-accent text-xs hover:bg-accent/10 transition-all duration-200">
            <span className="inline-block w-1.5 h-1.5 rounded-sm bg-accent animate-pulse" />
            停止生成
          </button>
        </div>
      )}

      {/* LLM 切换 + 多查询按钮 — 输入框正上方靠右 */}
      <div className="shrink-0 px-5 flex items-center justify-end gap-2">
        <MultiQueryToggle />
        <LLMSwitcher />
      </div>

      <ChatInput onSend={send} isLoading={isLoading} />
    </div>
  )
}
