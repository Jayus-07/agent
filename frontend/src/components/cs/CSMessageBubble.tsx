'use client'

import { Headphones } from 'lucide-react'
import MarkdownContent from '@/components/MarkdownContent'
import CSTimeline from './CSTimeline'
import type { CSMessage } from '@/store/csChat'

interface Props {
  message: CSMessage
  currentNode?: string | null
}

export default function CSMessageBubble({ message, currentNode }: Props) {
  if (message.role === 'user') {
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
          {message.content ? (
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
