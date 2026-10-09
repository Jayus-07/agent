'use client'

import { useState, useRef, useEffect, type KeyboardEvent } from 'react'
import { Send, Square } from 'lucide-react'

interface Props {
  onSend: (text: string) => void
  onStop: () => void
  isLoading: boolean
  /** 人工坐席已接管时，阻止客服窗口继续向 AI 业务管线发送。 */
  disabled?: boolean
  /** 输入内容变化回调（双向「输入中」指示上行，节流由调用方负责） */
  onTyping?: () => void
  /** 外部预填（多域隔离 M3：主图引导卡带来的 prefill_question）。
   *  nonce 变化即覆盖输入框内容，用户仍可修改后再发送。 */
  draft?: { text: string; nonce: number } | null
}

export default function CSInput({ onSend, onStop, isLoading, disabled = false, onTyping, draft }: Props) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (draft?.text) setText(draft.text)
  }, [draft?.nonce])

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px'
    }
  }, [text])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || isLoading || disabled) return
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
    <div className="shrink-0 border-t border-border-subtle bg-surface-base px-4 py-3">
      {/* 输入框独占整行：原布局在右侧挂了「AI 客服」标签，440px 抽屉内会把
          输入框挤窄、窄屏下标签自身还会换行；抽屉头部已标明身份，此处移除 */}
      <div className="flex items-end gap-2">
        <div className="flex-1 min-w-0 relative">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => {
              if (disabled) return
              setText(e.target.value)
              onTyping?.()
            }}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder={disabled ? '人工客服已接入，AI 输入已暂停' : '输入您的问题...'}
            rows={1}
            className="w-full resize-none rounded-xl border border-border-subtle bg-bg-root
              px-4 py-2.5 pr-10 text-sm text-text-primary placeholder:text-text-muted
              outline-none focus:border-accent/50 focus:shadow-input transition-all
              disabled:cursor-not-allowed disabled:bg-gray-100 disabled:opacity-70"
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
                disabled={disabled || !text.trim()}
                className="p-1.5 rounded-lg text-accent hover:bg-accent/10
                  disabled:text-text-muted/30 disabled:hover:bg-transparent transition-colors"
                title="发送"
              >
                <Send size={14} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
