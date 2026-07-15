'use client'

import { useEffect, useRef, useState } from 'react'
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

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  const isStreaming = useChatStore((s) => s.isLoading)
  const storeDeltaText = useChatStore((s) => s.deltaText)
  const storeStreamEvents = useChatStore((s) => s.streamEvents)
  const isCurrentStreaming = !isUser && isStreaming && !message.content

  const [renderText, setRenderText] = useState('')
  const rafRef = useRef<number | null>(null)
  const lastRenderedRef = useRef('')

  useEffect(() => {
    if (!isCurrentStreaming) return
    const schedule = () => {
      if (rafRef.current) return
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        if (storeDeltaText !== lastRenderedRef.current) {
          lastRenderedRef.current = storeDeltaText
          setRenderText(storeDeltaText)
        }
      })
    }
    schedule()
    return () => { if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null } }
  }, [storeDeltaText, isCurrentStreaming])

  const displayContent = isCurrentStreaming ? renderText || storeDeltaText : message.content
  const isEmpty = !displayContent && !message.content

  return (
    <div className={`animate-fade-in flex gap-3 group/message ${isUser ? 'justify-end' : ''}`}>
      {/* Assistant */}
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
            <MessageActions content={message.content || ''} isUser={true} isLast={false}
              onEdit={(t) => {/* TODO: wire to store */}} onResend={(t) => {/* TODO: wire to store */}} />
          </div>
        ) : (
          <div>
{/* Timeline: 已移至 ChatView 顶部 AgentTimeline */}
            {isEmpty && isCurrentStreaming ? (
              <div className="flex items-center gap-1.5 py-2">
                <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
                <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
                <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
              </div>
            ) : (
              <>
                {message.sources && message.sources.length > 0 && <SourceCard sources={message.sources} />}
                <div className="text-sm text-text-primary leading-relaxed">
                  <MarkdownContent content={
                    message.sources && message.sources.length > 0 ? stripReferences(displayContent) : displayContent
                  } />
                </div>
                {isCurrentStreaming && displayContent && (
                  <span className="inline-block w-0.5 h-4 bg-accent animate-pulse ml-0.5 align-text-bottom rounded-full" />
                )}
              </>
            )}
            {!isCurrentStreaming && displayContent && (
              <>
                {displayContent.includes('SELECT') && <SqlViz />}
                {displayContent.length > 200 && <TokenInfo />}
                <MessageActions content={displayContent} isUser={false} isLast={false}
                  onRegenerate={() => {/* TODO */}} />
              </>
            )}
          </div>
        )}
      </div>

      {/* User avatar */}
      {isUser && (
        <div className="w-7 h-7 rounded-full bg-accent flex items-center justify-center shrink-0 mt-0.5">
          <div className="text-white text-[10px] font-semibold">You</div>
        </div>
      )}
    </div>
  )
}
