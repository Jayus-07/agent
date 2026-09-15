'use client'

import { useState, useRef, useEffect, type KeyboardEvent } from 'react'
import { Send, Square, Headphones } from 'lucide-react'

interface Props {
  onSend: (text: string) => void
  onStop: () => void
  isLoading: boolean
}

export default function CSInput({ onSend, onStop, isLoading }: Props) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px'
    }
  }, [text])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || isLoading) return
    onSend(trimmed)
    setText('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="border-t border-border-subtle bg-surface-base px-4 py-3">
      <div className="max-w-3xl mx-auto flex items-end gap-2">
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入您的问题..."
            rows={1}
            className="w-full resize-none rounded-xl border border-border-subtle bg-bg-root
              px-4 py-2.5 pr-10 text-sm text-text-primary placeholder:text-text-muted
              outline-none focus:border-accent/50 focus:shadow-input transition-all"
          />
          <div className="absolute right-2 bottom-1.5 flex items-center gap-1">
            {isLoading ? (
              <button
                onClick={onStop}
                className="p-1.5 rounded-lg text-red-500 hover:bg-red-50 transition-colors"
                title="停止生成"
              >
                <Square size={14} fill="currentColor" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!text.trim()}
                className="p-1.5 rounded-lg text-accent hover:bg-accent/10
                  disabled:text-text-muted/30 disabled:hover:bg-transparent transition-colors"
                title="发送"
              >
                <Send size={14} />
              </button>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 pb-2">
          <Headphones size={14} className="text-text-muted" />
          <span className="text-[10px] text-text-muted">AI 客服</span>
        </div>
      </div>
    </div>
  )
}
