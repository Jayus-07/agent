'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { XCircle, AlertTriangle } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import { useSendMessage } from '@/hooks/useChat'
import MessageList from './MessageList'
import ChatInput from './ChatInput'
import EmptyState from './EmptyState'
import StatusBar from './StatusBar'
import LLMSwitcher from './LLMSwitcher'

export default function ChatView() {
  const messages = useChatStore((s) => s.currentMessages())
  const isLoading = useChatStore((s) => s.isLoading)
  const error = useChatStore((s) => s.error)
  const setError = useChatStore((s) => s.setError)
  const { send, stopStream } = useSendMessage()

  const bottomRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const [userScrolling, setUserScrolling] = useState(false)
  const scrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [reconnecting, setReconnecting] = useState(false)

  // 记录 SSE 错误用于重连提示
  useEffect(() => {
    if (error) {
      setReconnecting(true)
      const t = setTimeout(() => setReconnecting(false), 4000)
      return () => clearTimeout(t)
    }
  }, [error])

  // 自动滚动：用户手动上滚时暂缓，5s 无操作后恢复
  const handleScroll = useCallback(() => {
    const el = contentRef.current
    if (!el) return

    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (!atBottom) {
      setUserScrolling(true)
      if (scrollTimer.current) clearTimeout(scrollTimer.current)
      scrollTimer.current = setTimeout(() => setUserScrolling(false), 5000)
    } else {
      setUserScrolling(false)
    }
  }, [])

  // 新消息到达 → 自动滚动（除非用户正在查看历史）
  useEffect(() => {
    if (!userScrolling) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, messages[messages.length - 1]?.content, userScrolling])

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

      {/* 重连提示 */}
      {reconnecting && !error && (
        <div className="shrink-0 bg-amber-900/20 border-b border-amber-500/30 px-4 py-2 flex items-center gap-2 animate-fade-in">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" />
          <span className="text-sm text-amber-400 flex-1">
            网络波动，正在重试...
          </span>
        </div>
      )}

      {/* LLM 切换器 + 余额（Google 风格：透明背景 + 右对齐） */}
      <div className="shrink-0 px-5 py-2 flex items-center justify-end">
        <LLMSwitcher />
      </div>

      {/* 可滚动内容区 */}
      <div
        ref={contentRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto"
      >
        {messages.length === 0 ? (
          <EmptyState onExampleClick={send} />
        ) : (
          <MessageList messages={messages} isLoading={isLoading} />
        )}
        <div ref={bottomRef} />
      </div>

      {/* 顶部状态栏 */}
      <StatusBar />

      {/* 停止生成按钮 — 流式传输期间固定在底部 */}
      {isLoading && (
        <div className="shrink-0 flex justify-center pb-2">
          <button
            onClick={stopStream}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-red-500/10 border border-red-500/30 text-red-400 text-xs hover:bg-red-500/20 transition-colors"
          >
            <span className="inline-block w-2 h-2 rounded-sm bg-red-400" />
            ⏹ 停止生成
          </button>
        </div>
      )}

      <ChatInput onSend={send} isLoading={isLoading} />
    </div>
  )
}
