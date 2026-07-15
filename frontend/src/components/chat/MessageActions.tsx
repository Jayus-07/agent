'use client'

import { useState, useCallback } from 'react'
import { Copy, Check, RefreshCw, ThumbsUp, ThumbsDown, Pencil, Send } from 'lucide-react'

interface Props {
  content: string; isUser: boolean; isLast: boolean
  onRegenerate?: () => void; onEdit?: (text: string) => void; onResend?: (text: string) => void
}

export default function MessageActions({ content, isUser, isLast, onRegenerate, onEdit, onResend }: Props) {
  const [copied, setCopied] = useState(false)
  const [liked, setLiked] = useState<'up' | 'down' | null>(null)
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState(content)

  const handleCopy = useCallback(async () => {
    try { await navigator.clipboard.writeText(content) } catch { /* fallback */ }
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }, [content])

  if (editing) {
    return (
      <div className="flex items-start gap-2 mt-1">
        <textarea value={editText} onChange={e => setEditText(e.target.value)}
          className="flex-1 bg-surface-base border border-border-subtle rounded-lg px-3 py-1.5 text-xs text-text-primary resize-none outline-none focus:border-accent/40"
          rows={2} />
        <button onClick={() => { onEdit?.(editText); setEditing(false) }}
          className="shrink-0 p-1.5 rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors">
          <Send size={13} />
        </button>
        <button onClick={() => setEditing(false)}
          className="shrink-0 p-1.5 rounded-lg border border-border-subtle text-text-muted hover:text-text-secondary transition-colors text-[10px]">
          取消
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-0.5 mt-1.5 opacity-0 group-hover/message:opacity-100 transition-opacity duration-200">
      <ActionBtn icon={copied ? <Check size={12} className="text-green-500" /> : <Copy size={12} />}
        label={copied ? '已复制' : '复制'} onClick={handleCopy} />
      {isUser && isLast && (
        <>
          <ActionBtn icon={<Pencil size={12} />} label="编辑" onClick={() => { setEditText(content); setEditing(true) }} />
          <ActionBtn icon={<Send size={12} />} label="重发" onClick={() => onResend?.(content)} />
        </>
      )}
      {!isUser && (
        <>
          <ActionBtn icon={<RefreshCw size={12} />} label="重新生成" onClick={() => onRegenerate?.()} />
          <ActionBtn icon={<ThumbsUp size={12} className={liked === 'up' ? 'text-green-500' : ''} />}
            label="有用" onClick={() => setLiked(liked === 'up' ? null : 'up')} />
          <ActionBtn icon={<ThumbsDown size={12} className={liked === 'down' ? 'text-red-500' : ''} />}
            label="无用" onClick={() => setLiked(liked === 'down' ? null : 'down')} />
        </>
      )}
    </div>
  )
}

function ActionBtn({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-text-muted hover:text-text-secondary hover:bg-black/5 transition-colors"
      title={label}>
      {icon}{label}
    </button>
  )
}
