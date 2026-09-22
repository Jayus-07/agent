'use client'

/**
 * ChatInput — 两段式输入框：上方 textarea，下方 ComposerToolbar。
 *
 * 部门为只读展示（授权收口 §35）：账号部门由管理员维护、随 JWT 下发，
 * 本组件不再持有部门选择状态。
 */
import { useState, useRef, useEffect, KeyboardEvent } from 'react'
import ComposerToolbar from '@/components/agent/ComposerToolbar'
import {
  CHAT_INPUT_MAX_CHARS,
  chatInputOverLimit,
  chatInputShowCounter,
} from '@/lib/chatInputLimit'

interface Props {
  onSend: (text: string) => void
  isLoading: boolean
  /** 生成中时由输入框右下角按钮承担停止职责（ChatView 传入 stopStream） */
  onStop?: () => void
  /** 嵌在空状态居中组内（而非钉在会话底部）：去掉向上渐隐、收紧上下边距 */
  embedded?: boolean
  budgetBlocked?: boolean
}

export default function ChatInput({ onSend, isLoading, onStop, embedded = false, budgetBlocked = false }: Props) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 200) + 'px' }
  }, [input])

  function handleSend() {
    const trimmed = input.trim()
    if (!trimmed || isLoading || budgetBlocked) return
    // 超长输入禁止提交（后端仍是权威校验；此处只是避免必然失败的请求）
    if (chatInputOverLimit(trimmed)) return
    setInput(''); onSend(trimmed)
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  return (
    <div className={embedded ? '' : 'shrink-0 bg-gradient-to-t from-surface-root via-surface-root to-transparent'}>
      {/* embedded：整组（欢迎区+输入框）由外层 my-auto 居中，此处只留必要呼吸位，
          pt/pb 对称以免把居中组往下压 */}
      <div className={`max-w-3xl mx-auto px-4 ${embedded ? 'pt-5 pb-5' : 'pb-4 pt-2'}`}>
        <div className="bg-surface-base rounded-2xl px-4 pt-3 pb-2
          border border-border-subtle shadow-sm
          focus-within:border-accent/40 focus-within:shadow-input
          transition-all duration-250">
          {/* 上段：文本输入（min-h 让空态输入框更舒展，WorkBuddy 式两行视觉高度） */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="今天帮你做点什么？"
            rows={1}
            disabled={isLoading || budgetBlocked}
            className="w-full bg-transparent resize-none outline-none text-sm text-text-primary
              placeholder:text-text-muted min-h-[52px] max-h-[200px] disabled:opacity-40 leading-relaxed"
          />

          {/* 下段：工具栏 */}
          <ComposerToolbar
            disabled={isLoading || budgetBlocked}
            canSend={Boolean(input.trim()) && !chatInputOverLimit(input) && !isLoading && !budgetBlocked}
            onSend={handleSend}
            onStop={onStop}
          />
          {/* 接近/超过上限时显示计数与引导（短消息不展示噪声计数器） */}
          {chatInputShowCounter(input) && (
            <div className="flex items-center justify-between px-1 pt-1 text-[11px]">
              <span className={chatInputOverLimit(input) ? 'text-red-600' : 'text-text-muted'}>
                {chatInputOverLimit(input)
                  ? '输入过长，请缩短内容或通过知识库文件上传处理'
                  : '长文档建议通过知识库上传，可获得更好的检索与引用效果'}
              </span>
              <span className={chatInputOverLimit(input) ? 'text-red-600' : 'text-text-muted'}>
                {input.length} / {CHAT_INPUT_MAX_CHARS}
              </span>
            </div>
          )}
        </div>
        {budgetBlocked && <p className="mt-2 text-center text-[11px] text-red-600">硬额度已达到上限，发送和写操作暂时不可用；历史与只读页面仍可访问。</p>}
        <p className="text-[10px] text-text-muted text-center mt-2.5 select-none">
          Agent AI &middot; 答案由 AI 生成，请核实关键信息
        </p>
      </div>
    </div>
  )
}
