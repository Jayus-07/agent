'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import { PanelRightClose, Brain, MessageSquare } from 'lucide-react'

interface SessionMeta {
  session_id: string; title: string; message_count: number
  context_summary?: string; updated_at?: string
}

export default function HistorySidebar({ onClose }: { onClose: () => void }) {
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const currentId = useChatStore((s) => s.currentId)
  const router = useRouter()

  useEffect(() => {
    fetch('/api/memory/sessions').then(r => r.json()).then(d => setSessions(d.sessions || []))
  }, [])

  const handleSelect = (sid: string) => {
    router.push(`/agent?session=${sid}`)
  }

  return (
    <aside className="w-64 shrink-0 flex flex-col glass border-l border-black/5">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-text-primary">
          <Brain size={14} className="text-accent" />
          分析历史
        </div>
        <button onClick={onClose} className="p-1 rounded hover:bg-black/5 text-text-muted transition-colors" aria-label="关闭">
          <PanelRightClose size={15} />
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {sessions.length === 0 ? (
          <p className="text-xs text-text-muted px-2 py-6 text-center">暂无分析记录</p>
        ) : (
          <div className="space-y-0.5">
            {sessions.map(s => {
              const isActive = s.session_id === currentId
              const ctx = s.context_summary ? (() => { try { return JSON.parse(s.context_summary) } catch { return null } })() : null
              return (
                <button
                  key={s.session_id}
                  onClick={() => handleSelect(s.session_id)}
                  className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                    isActive ? 'bg-accent/8 text-accent' : 'hover:bg-black/5 text-text-secondary'
                  }`}>
                  <div className="text-[13px] font-medium truncate">{s.title}</div>
                  <div className="flex items-center gap-2 mt-1 text-[10px] text-text-muted">
                    <span className="flex items-center gap-1"><MessageSquare size={10} /> {s.message_count}</span>
                    {ctx?.turns ? <span>{ctx.turns} 轮</span> : null}
                    <span className="ml-auto">{s.updated_at?.slice(0, 10)}</span>
                  </div>
                  {ctx && (
                    <div className="flex items-center gap-1.5 mt-1 text-[10px]">
                      {ctx.sql_results ? <span className="text-accent">SQL×{ctx.sql_results}</span> : null}
                      {ctx.rag_docs ? <span className="text-green-500">RAG×{ctx.rag_docs}</span> : null}
                    </div>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </aside>
  )
}
