'use client'

/**
 * ChatInput — 两段式输入框：上方 textarea，下方 ComposerToolbar。
 *
 * 部门选择器的状态与持久化仍在本组件（决定 RAG 检索授权范围），
 * 工具栏只负责展示与回调，避免权限输入被搬到叶子组件后丢失。
 */
import { useState, useRef, useEffect, KeyboardEvent } from 'react'
import { getSelectedDepartment, setSelectedDepartment } from '@/lib/department'
import ComposerToolbar from '@/components/agent/ComposerToolbar'

interface Props {
  onSend: (text: string) => void
  isLoading: boolean
  /** 嵌在空状态居中组内（而非钉在会话底部）：去掉向上渐隐、收紧上下边距 */
  embedded?: boolean
}

export default function ChatInput({ onSend, isLoading, embedded = false }: Props) {
  const [input, setInput] = useState('')
  const [department, setDepartment] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 200) + 'px' }
  }, [input])

  // localStorage 仅客户端可读，挂载后再取，避免 SSR 水合不一致
  useEffect(() => {
    setDepartment(getSelectedDepartment())
  }, [])

  function handleDepartmentChange(value: string) {
    setDepartment(value)
    setSelectedDepartment(value)
  }

  function handleSend() {
    const trimmed = input.trim()
    if (!trimmed || isLoading) return
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
            disabled={isLoading}
            className="w-full bg-transparent resize-none outline-none text-sm text-text-primary
              placeholder:text-text-muted min-h-[52px] max-h-[200px] disabled:opacity-40 leading-relaxed"
          />

          {/* 下段：工具栏 */}
          <ComposerToolbar
            department={department}
            onDepartmentChange={handleDepartmentChange}
            disabled={isLoading}
            canSend={Boolean(input.trim()) && !isLoading}
            onSend={handleSend}
          />
        </div>
        <p className="text-[10px] text-text-muted text-center mt-2.5 select-none">
          Agent AI &middot; 答案由 AI 生成，请核实关键信息
        </p>
      </div>
    </div>
  )
}
