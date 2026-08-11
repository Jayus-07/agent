'use client'

/**
 * MessageBubble — 单条消息气泡
 *
 * 设计原则（P0-4 / P1-8 / P1-11）：
 *   1. 普通气泡不订阅流式字段：仅当 isLast && isLoading && !content 时进入"流式模式"，
 *      由 StreamingBubble 子组件订阅 storeDeltaText / storeStreamEvents。
 *      流式期间 store 更新只让最后一个 bubble 重渲；N-1 条历史气泡由 React.memo 跳过。
 *   2. 流式光标抽到独立组件，外层 re-render 不中断 CSS 动画。
 *   3. 用户气泡始终静态 → 不订阅任何 store 字段。
 */
import { memo, useEffect, useRef, useState } from 'react'
import type { Message } from '@/lib/types'
import { useChatStore } from '@/store/chat'
import SourceCard from './SourceCard'
import MarkdownContent from './MarkdownContent'
import MessageActions from './chat/MessageActions'
import SqlViz from './chat/SqlViz'
import TokenInfo from './chat/TokenInfo'

function stripReferences(content: string): string {
  const markers = ['\n\n---\n\n### 参考文献', '\n\n---\n\n### 参考来源',
                   '\n\n### 参考文献', '\n\n### 参考来源']
  for (const marker of markers) {
    const idx = content.indexOf(marker)
    if (idx !== -1) return content.slice(0, idx)
  }
  return content
}

/** 独立光标组件 —— 父级 re-render 不会中断 CSS 动画（P1-8） */
function StreamingCursor() {
  return (
    <span
      className="inline-block w-0.5 h-4 bg-accent ml-0.5 align-text-bottom rounded-full cursor-blink"
      aria-hidden
    />
  )
}

/**
 * 流式内容渲染 —— 只在"当前流式气泡"挂载一次，订阅 store 字段。
 *   非当前气泡（isLast=false 或非流式状态）不会加载此组件。
 */
function StreamingBubble({ message: _message }: { message: Message }) {
  // 单字段 selector：Zustand v5 不再支持自定义 equalityFn，引用稳定的 string 足够
  const deltaText = useChatStore((s) => s.deltaText)
  const [renderText, setRenderText] = useState('')
  const rafRef = useRef<number | null>(null)
  const lastRenderedRef = useRef('')

  useEffect(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      if (deltaText !== lastRenderedRef.current) {
        lastRenderedRef.current = deltaText
        setRenderText(deltaText)
      }
    })
    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [deltaText])

  const displayContent = renderText || deltaText
  return (
    <div>
      {displayContent ? (
        <div className="text-sm text-text-primary leading-relaxed">
          <MarkdownContent content={displayContent} />
          <StreamingCursor />
        </div>
      ) : (
        <div className="flex items-center gap-1.5 py-2">
          <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
          <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
          <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
        </div>
      )}
    </div>
  )
}

interface MessageBubbleProps {
  message: Message
  isLast: boolean
  sessionId?: string
  question?: string
}

function MessageBubbleImpl({ message, isLast, sessionId, question }: MessageBubbleProps) {
  const isUser = message.role === 'user'
  // 只订阅 isLoading —— 单字段、引用稳定；其余流式字段由 StreamingBubble 单独订阅
  const isLoading = useChatStore((s) => s.isLoading)
  // 流式模式：最后一条 assistant + 加载中 + 当前消息还没写入完成内容
  const isCurrentStreaming = !isUser && isLoading && isLast && !message.content

  return (
    <div className={`animate-fade-in flex gap-3 group/message ${isUser ? 'justify-end' : ''}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-accent/8 flex items-center justify-center shrink-0 mt-0.5">
          <div className="w-2 h-2 rounded-full bg-accent" />
        </div>
      )}

      <div className={`min-w-0 ${isUser ? 'max-w-[75%]' : 'max-w-[80%]'}`}>
        {isUser ? (
          <div>
            <div className="bg-accent text-white rounded-2xl rounded-br-md px-4 py-2.5 text-sm leading-relaxed shadow-sm">
              {message.content}
            </div>
            <MessageActions
              content={message.content || ''}
              isUser
              isLast={isLast}
              sessionId={sessionId}
              msgId={message.id}
              onEdit={() => {/* TODO: wire to store */}}
              onResend={() => {/* TODO: wire to store */}}
            />
          </div>
        ) : (
          <div>
            {isCurrentStreaming ? (
              <StreamingBubble message={message} />
            ) : (
              <>
                {message.sources && message.sources.length > 0 && (
                  <SourceCard sources={message.sources} />
                )}
                <div className="text-sm text-text-primary leading-relaxed">
                  <MarkdownContent
                    content={
                      message.sources && message.sources.length > 0
                        ? stripReferences(message.content)
                        : message.content
                    }
                  />
                </div>
              </>
            )}

            {/* 流式结束后才显示附属信息（SqlViz/TokenInfo/Actions） */}
            {!isCurrentStreaming && message.content && (
              <>
                <SqlViz streamEvents={message.streamEvents ?? []} />
                <TokenInfo streamEvents={message.streamEvents ?? []} />
                <MessageActions
                  content={message.content}
                  isUser={false}
                  isLast={isLast}
                  sessionId={sessionId}
                  msgId={message.id}
                  question={question}
                  onRegenerate={() => {/* TODO: wire to store */}}
                />
              </>
            )}
          </div>
        )}
      </div>

      {isUser && (
        <div className="w-7 h-7 rounded-full bg-accent flex items-center justify-center shrink-0 mt-0.5">
          <div className="text-white text-[10px] font-semibold">You</div>
        </div>
      )}
    </div>
  )
}

// memo：message / isLast prop 不变则跳过 re-render ——
// store 在流式期间 sessions 引用变，会让 MessageList 重渲染，但只要 message 引用稳定，
// N-1 条历史气泡就被 memo 拦截，避免 MarkdownContent 重复解析整篇。
const MessageBubble = memo(MessageBubbleImpl, (prev, next) =>
  prev.isLast === next.isLast && prev.message === next.message,
)

export default MessageBubble
