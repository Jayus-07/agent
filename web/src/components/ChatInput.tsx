'use client'

import { useState, useRef, useEffect, KeyboardEvent } from 'react'
import { Send, Loader2 } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import { useSendMessage } from '@/hooks/useChat'

export default function ChatInput() {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isLoading = useChatStore((s) => s.isLoading)
  const { send } = useSendMessage()

  // 自动调整 textarea 高度
  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
  }, [input])

  function handleSend() {
    const trimmed = input.trim()
    if (!trimmed || isLoading) return
    setInput('')
    send(trimmed)
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="shrink-0 border-t border-[#3f3f3f] bg-[#212121]">
      <div className="max-w-3xl mx-auto px-4 py-3">
        <div className="flex items-end gap-3 bg-[#2f2f2f] rounded-2xl px-4 py-2.5 border border-[#3f3f3f] focus-within:border-[#5f5f5f] transition-colors">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入你的问题... (Enter 发送, Shift+Enter 换行)"
            rows={1}
            disabled={isLoading}
            className="flex-1 bg-transparent resize-none outline-none text-sm text-[#ececec] placeholder-[#8e8e8e] max-h-[200px] disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className="shrink-0 p-1.5 rounded-lg bg-[#ececec] text-[#171717] hover:bg-white disabled:opacity-30 disabled:cursor-not-allowed transition-all"
            aria-label="发送"
          >
            {isLoading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
          </button>
        </div>
        <p className="text-[10px] text-[#8e8e8e] text-center mt-2">
          Agent AI 基于 LangGraph Multi-Agent 架构 &middot; 答案由 LLM 生成，请核实关键信息
        </p>
      </div>
    </div>
  )
}
