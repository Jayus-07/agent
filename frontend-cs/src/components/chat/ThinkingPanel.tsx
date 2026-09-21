'use client'

/**
 * ThinkingPanel — "已思考"折叠面板（DeepSeek 风格）
 *
 * - 头部：灰色小字 "已思考（用时 N 秒）" / 思考中（进行时），点击展开收起
 * - 正文：思考链原文，灰色小字 + 左侧竖线，限高内部滚动
 * - live 模式：思考进行中默认展开；首块回答到达（phase → answering）自动收起；
 *   历史消息（live=false）默认收起
 */
import { useEffect, useRef, useState } from 'react'
import { Brain, ChevronDown } from 'lucide-react'

interface Props {
  text: string
  /** 思考耗时（秒）；null = 思考未走完（进行中或被中止） */
  seconds?: number | null
  /** true = 本轮流式进行中 */
  live?: boolean
  /** 回答是否已开始输出（live 时用于自动收起） */
  answering?: boolean
}

/** 面板标题文案（导出供测试）：思考中 → 已思考（用时 N 秒）→ 已思考 */
export function thinkingLabel(live: boolean, answering: boolean, seconds: number | null): string {
  if (live && !answering) return '思考中…'
  return seconds != null ? `已思考（用时 ${seconds} 秒）` : '已思考'
}

export default function ThinkingPanel({ text, seconds = null, live = false, answering = false }: Props) {
  const [open, setOpen] = useState(live && !answering)
  const bodyRef = useRef<HTMLDivElement>(null)
  // 用户手动展开后，回答开始时不再强制收起（尊重用户意图）
  const userToggledRef = useRef(false)

  useEffect(() => {
    if (live && answering && !userToggledRef.current) setOpen(false)
  }, [live, answering])

  // 展开状态下思考链持续流入 → 跟随滚动到底部
  useEffect(() => {
    if (open && live && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [open, live, text])

  if (!text) return null

  const label = thinkingLabel(live, answering, seconds)

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => { userToggledRef.current = true; setOpen((v) => !v) }}
        className="flex items-center gap-1.5 py-0.5 text-xs text-text-muted hover:text-text-secondary transition-colors"
        aria-expanded={open}
      >
        <Brain size={13} />
        <span>{label}</span>
        <ChevronDown
          size={13}
          className={`transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          ref={bodyRef}
          className="mt-1 max-h-48 overflow-y-auto border-l-2 border-black/10 pl-3 py-1
            text-xs leading-relaxed text-text-muted whitespace-pre-wrap break-words"
        >
          {text}
        </div>
      )}
    </div>
  )
}
