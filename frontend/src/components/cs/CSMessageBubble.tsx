'use client'

import { memo, useEffect, useRef, useState } from 'react'
import { Headphones } from 'lucide-react'
import MarkdownContent from '@/components/MarkdownContent'
import CSTimeline from './CSTimeline'
import type { CSMessage } from '@/store/csChat'
import { useCSChatStore } from '@/store/csChat'

/** 独立光标组件 —— 父级 re-render 不中断 CSS 动画 */
function StreamingCursor() {
  return (
    <span
      className="inline-block w-0.5 h-4 bg-accent ml-0.5 align-text-bottom rounded-full cursor-blink"
      aria-hidden
    />
  )
}

/**
 * 流式内容渲染 —— 只在"当前流式气泡"挂载，订阅 csChat store 的 deltaText。
 * rAF 节流：一帧内多条 delta 只触发一次 Markdown 解析。
 */
function CSStreamingContent() {
  const deltaText = useCSChatStore((s) => s.deltaText)
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
    <div className="text-sm text-text-primary leading-relaxed">
      {displayContent ? (
        <>
          <MarkdownContent content={displayContent} />
          <StreamingCursor />
        </>
      ) : (
        <span className="inline-flex items-center gap-1 text-text-muted">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce" />
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:0.15s]" />
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:0.3s]" />
        </span>
      )}
    </div>
  )
}

interface Props {
  message: CSMessage
  currentNode?: string | null
  isLast?: boolean
}

function CSMessageBubbleImpl({ message, currentNode, isLast }: Props) {
  // 只订阅 isLoading —— 流式文本由 CSStreamingContent 单独订阅 deltaText
  const isLoading = useCSChatStore((s) => s.isLoading)
  const isUser = message.role === 'user'
  // 流式模式：最后一条 assistant + 加载中 + 完整内容尚未写入（done 时才 replaceLastAssistant）
  const isCurrentStreaming = !isUser && isLoading && isLast && !message.content

  if (isUser) {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[75%] px-4 py-2.5 rounded-2xl rounded-br-md
          bg-accent text-white text-sm leading-relaxed">
          {message.content}
        </div>
      </div>
    )
  }

  const hasNodes = message.csNodes && message.csNodes.length > 0

  return (
    <div className="flex gap-3 mb-4">
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center">
        <Headphones size={16} className="text-accent" />
      </div>
      <div className="flex-1 min-w-0 max-w-[80%]">
        <div className="bg-surface-base border border-border-subtle rounded-2xl rounded-tl-md px-4 py-3 text-sm text-text-primary leading-relaxed">
          {isCurrentStreaming ? (
            <CSStreamingContent />
          ) : message.content ? (
            <MarkdownContent content={message.content} />
          ) : (
            <span className="inline-flex items-center gap-1 text-text-muted">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce" />
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:0.15s]" />
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:0.3s]" />
            </span>
          )}
        </div>
        {hasNodes && (
          <CSTimeline nodes={message.csNodes!} currentNode={currentNode} />
        )}
      </div>
    </div>
  )
}

// memo：流式期间 csChat 每个 delta 都重建 sessions/messages 数组，
// message 引用未变的历史气泡靠 memo 拦截，避免 MarkdownContent 整篇重复解析
const CSMessageBubble = memo(CSMessageBubbleImpl)

export default CSMessageBubble
