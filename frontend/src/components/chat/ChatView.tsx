'use client'

import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useSearchParams } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import { useSendMessage } from '@/hooks/useChat'
import type { Message } from '@/lib/types'
import MessageList from './MessageList'
import ChatInput from './ChatInput'
import WelcomeState from './WelcomeState'
import ContextPanel from './ContextPanel'
import ErrorCard from '@/components/shared/ErrorCard'

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
      {/* Error card（X3 语义化）：存原始异常对象 → ErrorCard 解析语义化文案
          + 行动指引 + 详情折叠；historyError 在任务栏显示（P1-16），此处只管轮次错误 */}
      {error !== null && (
        <div className="shrink-0 mx-5 mt-3 flex items-start gap-2.5 animate-fade-in">
          <ErrorCard error={error} className="flex-1 max-w-none" />
          <button
            onClick={() => useChatStore.getState().setError(null)}
            className="text-xs text-gray-400 hover:text-gray-600 shrink-0 mt-1 transition-colors"
          >
            关闭
          </button>
        </div>
      )}

      {/* Memory context panel */}
      <ContextPanel sessionId={currentId} />

      {/* Messages —— Agent 进度卡片（时间线/任务清单/产出文件）已嵌入消息流：
          最后一轮提问之下、回答之上，由 MessageList 控制，流结束自动收起 */}

      {/* 空状态：欢迎区 + 输入框作为【同一个居中组】垂直居中（对齐 WorkBuddy 工作台）——
          若沿用「输入框置底」的对话态布局，欢迎区在剩余空间里居中会让两者之间
          拉出一大片空白。此处两者同处一个 flex 列，用 my-auto 整体居中。 */}
      {messages.length === 0 ? (
        <div className="flex-1 min-h-0 flex flex-col px-6 py-6">
          <div className="w-full my-auto">
            <WelcomeState onExampleClick={send} />
            <ChatInput onSend={send} isLoading={isLoading} onStop={stopStream} embedded />
          </div>
        </div>
      ) : (
        <>
          <div ref={contentRef} onScroll={handleScroll} className="flex-1 overflow-y-auto">
            <MessageList messages={messages} isLoading={isLoading} sessionId={currentId} />
            <div ref={bottomRef} />
          </div>
          <ChatInput onSend={send} isLoading={isLoading} onStop={stopStream} />
        </>
      )}
    </div>
  )
}
