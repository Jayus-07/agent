'use client'

/**
 * SessionRow — 会话列表单行（含内联重命名、hover 删除）
 *
 * 从 HistorySidebar 抽出，供 HistorySidebar（回滚用）与 TaskSidebar 共用。
 * 宽度自适应：外框不设固定宽度，由父容器决定；hover 操作按钮绝对定位在右侧。
 */
import { useEffect, useRef, useState } from 'react'
import { Loader2, MessageSquare, Pencil, Trash2 } from 'lucide-react'
import type { SessionMeta } from '@/api/memory'
import { parseContextSummary } from '@/lib/context-summary'
import { formatTime } from '@/lib/session-groups'

export interface SessionRowProps {
  session: SessionMeta
  isActive: boolean
  /** 该会话正在流式生成中：时间位置显示转圈（WorkBuddy 式进行中标记） */
  running?: boolean
  onSelect: () => void
  onRename: (title: string) => void
  onDelete: () => void
  activeRef?: (el: HTMLButtonElement | null) => void
}

export default function SessionRow({
  session: s, isActive, running, onSelect, onRename, onDelete, activeRef,
}: SessionRowProps) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(s.title)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  const commitRename = () => {
    const trimmed = title.trim()
    if (trimmed && trimmed !== s.title) {
      onRename(trimmed)
    } else {
      setTitle(s.title)
    }
    setEditing(false)
  }

  const ctx = parseContextSummary(s.context_summary)

  return (
    <div
      className={`group relative rounded-lg transition-colors ${
        isActive ? 'bg-accent/8' : 'hover:bg-black/5'
      }`}
    >
      {editing ? (
        <div className="px-2 py-2">
          <input
            ref={inputRef}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename()
              if (e.key === 'Escape') {
                setTitle(s.title)
                setEditing(false)
              }
            }}
            onClick={(e) => e.stopPropagation()}
            className="w-full bg-white border border-black/10 rounded px-2 py-1 text-[13px] text-text-primary outline-none focus:border-accent/50"
          />
        </div>
      ) : (
        <button
          ref={activeRef}
          onClick={onSelect}
          className={`w-full text-left px-3 py-1.5 pr-14 rounded-lg transition-colors ${
            isActive ? 'text-accent' : 'text-text-secondary'
          }`}
        >
          <div className="text-[13px] font-medium truncate">{s.title}</div>
          <div className="flex items-center gap-2 mt-0.5 text-[10px] text-text-muted">
            <span className="flex items-center gap-1">
              <MessageSquare size={10} /> {s.message_count}
            </span>
            {ctx?.turns ? <span>{ctx.turns} 轮</span> : null}
            <span className="ml-auto flex items-center gap-1">
              {running ? (
                <span className="flex items-center gap-1 text-accent" title="生成中">
                  <Loader2 size={10} className="animate-spin" />
                  生成中
                </span>
              ) : (
                formatTime(s.updated_at)
              )}
            </span>
          </div>
          {ctx && (ctx.sql_results || ctx.rag_docs) && (
            <div className="flex items-center gap-1.5 mt-1 text-[10px]">
              {ctx.sql_results ? <span className="text-accent">SQL×{ctx.sql_results}</span> : null}
              {ctx.rag_docs ? <span className="text-green-500">RAG×{ctx.rag_docs}</span> : null}
            </div>
          )}
        </button>
      )}

      {/* hover 操作按钮（编辑态下隐藏，避免遮挡输入框） */}
      {!editing && (
        <div className="absolute right-2 top-2 hidden group-hover:flex items-center gap-0.5">
          <button
            onClick={(e) => {
              e.stopPropagation()
              setTitle(s.title)
              setEditing(true)
            }}
            className="p-1 rounded hover:bg-black/10 text-text-muted hover:text-text-primary transition-colors"
            aria-label="重命名"
            title="重命名"
          >
            <Pencil size={12} />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              onDelete()
            }}
            className="p-1 rounded hover:bg-black/10 text-text-muted hover:text-red-500 transition-colors"
            aria-label="删除"
            title="删除"
          >
            <Trash2 size={12} />
          </button>
        </div>
      )}
    </div>
  )
}
