'use client'

import { useState, useRef, useEffect, KeyboardEvent } from 'react'
import { ArrowUp, Building2 } from 'lucide-react'
import { DEPARTMENTS, getSelectedDepartment, setSelectedDepartment } from '@/lib/department'

interface Props { onSend: (text: string) => void; isLoading: boolean }

export default function ChatInput({ onSend, isLoading }: Props) {
  const [input, setInput] = useState('')
  const [department, setDepartment] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px' }
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
    <div className="shrink-0 bg-gradient-to-t from-surface-root via-surface-root to-transparent">
      <div className="max-w-[720px] mx-auto px-4 pb-4 pt-2">
        <div className="relative flex items-end gap-3 bg-surface-base rounded-2xl px-4 py-3
          border border-border-subtle shadow-sm
          focus-within:border-accent/40 focus-within:shadow-input
          transition-all duration-250">
          {/* 部门选择：决定检索授权范围（空 = 按对客最严格集合） */}
          <div
            className="shrink-0 flex items-center gap-1 rounded-xl bg-black/[0.04] hover:bg-black/[0.07]
              transition-colors duration-200 px-2.5 py-2"
            title="选择部门以获得对应知识库的检索范围；未选择按对客最严格范围处理"
          >
            <Building2 size={14} className="text-text-muted" aria-hidden />
            <select
              value={department}
              onChange={(e) => handleDepartmentChange(e.target.value)}
              disabled={isLoading}
              aria-label="选择部门（检索授权范围）"
              className="bg-transparent outline-none text-xs text-text-primary cursor-pointer
                disabled:opacity-40 max-w-[88px] appearance-none"
            >
              <option value="">未选择</option>
              {DEPARTMENTS.map((d) => (
                <option key={d.id} value={d.id}>{d.label}</option>
              ))}
            </select>
          </div>
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
