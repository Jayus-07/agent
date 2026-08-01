'use client'

import { useState, useRef, useEffect, KeyboardEvent } from 'react'
import { ArrowUp } from 'lucide-react'

interface Props { onSend: (text: string) => void; isLoading: boolean }

export default function ChatInput({ onSend, isLoading }: Props) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px' }
  }, [input])

  function handleSend() {
    const trimmed = input.trim()
    if (!trimmed || isLoading) return
    setInput(''); onSend(trimmed)
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  return (
    <div className="shrink-0 bg-gradient-to-t from-surface-root via-surface-root to-transparent">
      <div className="max-w-[720px] mx-auto px-4 pb-4 pt-2">
        <div className="relative flex items-end gap-3 bg-surface-base rounded-2xl px-4 py-3
          border border-border-subtle shadow-sm
          focus-within:border-accent/40 focus-within:shadow-input
          transition-all duration-250">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入你的问题..."
            rows={1}
            disabled={isLoading}
            className="flex-1 bg-transparent resize-none outline-none text-sm text-text-primary
              placeholder-text-muted max-h-[160px] disabled:opacity-40 leading-relaxed"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className="shrink-0 w-8 h-8 rounded-xl bg-accent text-white flex items-center justify-center
              hover:bg-accent-hover disabled:opacity-20 disabled:cursor-not-allowed
              transition-all duration-200 active:scale-95"
            aria-label="发送">
            <ArrowUp size={16} strokeWidth={2.5} />
          </button>
        </div>
        <p className="text-[10px] text-text-muted text-center mt-2.5 select-none">
          Agent AI &middot; 答案由 AI 生成，请核实关键信息
        </p>
      </div>
    </div>
  )
}
