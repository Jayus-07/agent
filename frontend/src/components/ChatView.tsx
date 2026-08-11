'use client'

import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useSearchParams } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import { useSendMessage } from '@/hooks/useChat'
import type { Message } from '@/lib/types'
import MessageList from './MessageList'
import ChatInput from './ChatInput'
import EmptyState from './EmptyState'
import StatusBar from './StatusBar'
import LLMSwitcher from './LLMSwitcher'
import MultiQueryToggle from './MultiQueryToggle'
import ContextPanel from './chat/ContextPanel'

// 模块级稳定空数组，避免 messages 为空时 useMemo 每次返回新 []
const EMPTY_MESSAGES: Message[] = []

export default function ChatView() {
  // ⚠️ 不要在 Zustand selector 里调函数 —— 每次 store 任意字段变化都会重跑 selector，
  // 函数返回新数组/新对象 → React 检测到引用变化 → re-render → 触发 store 更新 → 无限循环。
  // 正确做法：只选原始字段（引用稳定），用 useMemo 在组件内派生。
  const sessions = useChatStore((s) => s.sessions)
  const currentId = useChatStore((s) => s.currentId)
  const messages = useMemo(
    () => sessions.find((s) => s.id === currentId)?.messages ?? EMPTY_MESSAGES,
    [sessions, currentId],
  )
  const isLoading = useChatStore((s) => s.isLoading)
  const error = useChatStore((s) => s.error)
  const [timelineOpen, setTimelineOpen] = useState(true)
  const { send, stopStream } = useSendMessage()

  const bottomRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const [userScrolling, setUserScrolling] = useState(false)
  const scrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // P2: 卸载/依赖变化时清理定时器，避免 setState on unmounted
  const clearScrollTimer = useCallback(() => {
    if (scrollTimer.current) {
      clearTimeout(scrollTimer.current)
      scrollTimer.current = null
    }
  }, [])
  useEffect(() => clearScrollTimer, [clearScrollTimer])

  // P1-9: 改用 ResizeObserver 监听内容高度增长，而不是 [messages, content, userScrolling] 三依赖
  // —— 后者会让 smooth scroll 多次排队造成视觉抖动；
  // ResizeObserver 只在内容高度变化时触发，且 throttle 后只滚一次。
  useEffect(() => {
    const el = contentRef.current
    if (!el) return
    let rafScheduled = false
    const ro = new ResizeObserver(() => {
      if (userScrolling) return
      if (rafScheduled) return
      rafScheduled = true
      requestAnimationFrame(() => {
        rafScheduled = false
        // 只滚一次到容器底部
        bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
      })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [userScrolling])

  const handleScroll = useCallback(() => {
    const el = contentRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (!atBottom) {
      setUserScrolling(true)
      clearScrollTimer()
      scrollTimer.current = setTimeout(() => setUserScrolling(false), 4000)
    } else {
      setUserScrolling(false)
    }
  }, [clearScrollTimer])

  const searchParams = useSearchParams()
  const loadHistory = useChatStore((s) => s.loadHistory)
  const switchSession = useChatStore((s) => s.switchSession)

  // P1-7: 依赖改为字符串而非 searchParams 对象（避免某些 Next.js 实现版本下
  // URLSearchParams 引用变化引起 loadHistory 重入）
  const sessionParam = searchParams.get('session')
  useEffect(() => {
    if (sessionParam) {
      switchSession(sessionParam)
      loadHistory(sessionParam)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionParam])

  return (
    <div className="flex-1 flex flex-col min-h-0 relative">
      {/* Error toast — P1-16: historyError 已迁移至 HistorySidebar，避免与 sidebar 错误双显示 */}
      {error && (
        <div className="shrink-0 mx-5 mt-3 bg-red-50 border border-red-200 rounded-xl px-4 py-3 flex items-center gap-2.5 animate-fade-in">
          <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />
          <span className="text-sm text-red-700 flex-1">{error}</span>
          <button
            onClick={() => useChatStore.getState().setError(null)}
            className="text-xs text-red-400 hover:text-red-600 shrink-0 transition-colors"
          >
            关闭
          </button>
        </div>
      )}

      {/* Memory context panel */}
      <ContextPanel sessionId={currentId} />

      {/* Messages */}
      <div ref={contentRef} onScroll={handleScroll} className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState onExampleClick={send} />
        ) : (
          <MessageList messages={messages} isLoading={isLoading} sessionId={currentId} />
        )}
        <div ref={bottomRef} />
      </div>

      {/* Status */}
      <StatusBar />

      {/* Stop button */}
      {isLoading && (
        <div className="shrink-0 flex justify-center pb-2">
          <button
            type="button"
            onClick={stopStream}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-accent/5 border border-accent/20 text-accent text-xs hover:bg-accent/10 transition-all duration-200"
          >
            <span className="inline-block w-1.5 h-1.5 rounded-sm bg-accent animate-pulse" />
            停止生成
          </button>
        </div>
      )}

      {/* 工具栏 — 输入框正上方 */}
      <div className="shrink-0 max-w-[720px] mx-auto w-full px-4 pb-1 flex items-center justify-end gap-2">
        <MultiQueryToggle />
        <LLMSwitcher />
      </div>

      <ChatInput onSend={send} isLoading={isLoading} />
    </div>
  )
}
