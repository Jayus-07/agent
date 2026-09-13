'use client'

import { memo } from 'react'
import { Headphones } from 'lucide-react'
import MarkdownContent from '@/components/MarkdownContent'
import CSTimeline from './CSTimeline'
import type { CSMessage } from '@/store/csChat'
import { useCSChatStore } from '@/store/csChat'
import StreamingContent from '@/components/chat/StreamingContent'

/** 绑定 csChat store 的流式文本订阅（StreamingContent 经 props 接收 hook） */
function useCSDelta(): string {
  return useCSChatStore((s) => s.deltaText)
}

interface Props {
  message: CSMessage
  currentNode?: string | null
  isLast?: boolean
}

function CSMessageBubbleImpl({ message, currentNode, isLast }: Props) {
  // 只订阅 isLoading —— 流式文本由共享 StreamingContent 单独订阅 deltaText
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
            <StreamingContent useDeltaText={useCSDelta} />
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

// memo：流式期间 store 的 sessions 数组会因新增消息/终态写入而重建，
// message 引用未变的历史气泡靠 memo 拦截，避免 MarkdownContent 整篇重复解析
const CSMessageBubble = memo(CSMessageBubbleImpl)

export default CSMessageBubble
