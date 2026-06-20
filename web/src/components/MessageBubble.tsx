'use client'

import { useEffect, useRef, useState } from 'react'
import { User, Bot } from 'lucide-react'
import type { Message } from '@/lib/types'
import { useChatStore } from '@/store/chat'
import ThinkingPanel from './ThinkingPanel'
import SourceCard from './SourceCard'
import MarkdownContent from './MarkdownContent'

/** 从正文末尾剥离 "### 参考文献" 区块（SourceCard 已展示，避免重复） */
function stripReferences(content: string): string {
  const markers = ['\n\n---\n\n### 参考文献', '\n\n---\n\n### 参考来源',
                   '\n\n### 参考文献', '\n\n### 参考来源']
  for (const marker of markers) {
    const idx = content.indexOf(marker)
    if (idx !== -1) {
      return content.slice(0, idx)
    }
  }
  return content
}

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  const isStreaming = useChatStore((s) => s.isLoading)
  const storeDeltaText = useChatStore((s) => s.deltaText)
  const storeStreamEvents = useChatStore((s) => s.streamEvents)

  // 是否为当前正在流式输出的 assistant 消息
  const isCurrentStreaming = !isUser && isStreaming && !message.content

  // RAF 节流：delta 高频推送时防止 DOM 卡顿
  const [renderText, setRenderText] = useState('')
  const rafRef = useRef<number | null>(null)
  const lastRenderedRef = useRef('')

  useEffect(() => {
    if (!isCurrentStreaming) return

    const schedule = () => {
      if (rafRef.current) return  // 已有待处理帧
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        if (storeDeltaText !== lastRenderedRef.current) {
          lastRenderedRef.current = storeDeltaText
          setRenderText(storeDeltaText)
        }
      })
    }

    schedule()
    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [storeDeltaText, isCurrentStreaming])

  // 最终内容：done 后使用 message.content，流式中使用 RAF 节流文本
  const displayContent = isCurrentStreaming
    ? renderText || storeDeltaText
    : message.content

  // 流式传输中且无 delta 文本 → 空态
  const isEmpty = !displayContent && !message.content

  return (
    <div className={`animate-fade-in flex gap-4 ${isUser ? 'justify-end' : ''}`}>
      {/* 头像 */}
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-emerald-600 flex items-center justify-center shrink-0">
          <Bot size={18} className="text-white" />
        </div>
      )}

      <div className={`min-w-0 max-w-[85%] ${isUser ? 'order-first' : ''}`}>
        {/* 用户消息 */}
        {isUser ? (
          <div className="bg-[#2f2f2f] rounded-2xl rounded-br-md px-4 py-3 text-[#ececec] text-sm leading-relaxed">
            {message.content}
          </div>
        ) : (
          <div>
            {/* SSE v2: 思维链日志面板（底部折叠） */}
            {storeStreamEvents.length > 0 && <ThinkingPanel />}

            {/* 流式传输中：打字机渲染 delta 文本 */}
            {isEmpty && isCurrentStreaming ? (
              <div className="text-sm text-[#ececec] py-1 animate-pulse">
                <span className="text-[#8e8e8e]">⏳ 等待响应...</span>
              </div>
            ) : (
              <>
                {message.sources && message.sources.length > 0 && (
                  <SourceCard sources={message.sources} />
                )}
                <div className="text-sm text-[#ececec]">
                  <MarkdownContent
                    content={
                      message.sources && message.sources.length > 0
                        ? stripReferences(displayContent)
                        : displayContent
                    }
                  />
                </div>
                {/* 流式中，Markdown 后追加光标闪烁 */}
                {isCurrentStreaming && displayContent && (
                  <span className="inline-block w-0.5 h-4 bg-[#ececec] animate-pulse ml-0.5 align-text-bottom" />
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* 用户头像 */}
      {isUser && (
        <div className="w-8 h-8 rounded-full bg-violet-600 flex items-center justify-center shrink-0">
          <User size={16} className="text-white" />
        </div>
      )}
    </div>
  )
}
