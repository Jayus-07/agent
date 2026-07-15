'use client'

import { useState, useRef, useEffect } from 'react'
import { Trash2, Pencil } from 'lucide-react'
import { clsx } from 'clsx'
import type { Session } from '@/lib/types'

interface Props {
  session: Session
  isActive: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (title: string) => void
}

export default function SessionItem({ session, isActive, onSelect, onDelete, onRename }: Props) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(session.title)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  function commitRename() {
    const trimmed = title.trim()
    if (trimmed && trimmed !== session.title) {
      onRename(trimmed)
    } else {
      setTitle(session.title)
    }
    setEditing(false)
  }

  return (
    <li
      className={clsx(
        'group flex items-center rounded-lg px-2 py-2 text-sm cursor-pointer transition-colors',
        isActive ? 'bg-[#2f2f2f] text-[#ececec]' : 'text-[#b4b4b4] hover:bg-[#262626]'
      )}
      onClick={() => !editing && onSelect()}
    >
      {editing ? (
        <input
          ref={inputRef}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename()
            if (e.key === 'Escape') {
              setTitle(session.title)
              setEditing(false)
            }
          }}
          onClick={(e) => e.stopPropagation()}
          className="flex-1 bg-[#171717] border border-[#3f3f3f] rounded px-1.5 py-0.5 text-xs text-[#ececec] outline-none focus:border-[#5f5f5f]"
        />
      ) : (
        <span className="flex-1 truncate text-xs">{session.title}</span>
      )}

      {/* 操作按钮（hover 时显示） */}
      {!editing && (
        <div className="hidden group-hover:flex items-center gap-0.5 ml-1 shrink-0">
          <button
            onClick={(e) => {
              e.stopPropagation()
              setEditing(true)
              setTitle(session.title)
            }}
            className="p-0.5 rounded hover:bg-[#3f3f3f] text-[#8e8e8e] hover:text-[#ececec] transition-colors"
            aria-label="重命名"
          >
            <Pencil size={12} />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              onDelete()
            }}
            className="p-0.5 rounded hover:bg-[#3f3f3f] text-[#8e8e8e] hover:text-red-400 transition-colors"
            aria-label="删除"
          >
            <Trash2 size={12} />
          </button>
        </div>
      )}
    </li>
  )
}
