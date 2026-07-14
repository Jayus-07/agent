'use client'

'use client'

import { useEffect, useState } from 'react'
import { CheckCircle2, ArrowRight, Brain } from 'lucide-react'
import { useRouter } from 'next/navigation'

interface SessionMeta {
  session_id: string; title: string; message_count: number
  context_summary?: string; updated_at?: string; created_at?: string
}

export default function TasksPage() {
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const router = useRouter()

  useEffect(() => {
    fetch('/api/memory/sessions').then(r => r.json()).then(d => setSessions(d.sessions || []))
  }, [])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">分析历史</h1>
          <p className="text-xs text-text-muted mt-1">{sessions.length} 次分析任务 · 点击可恢复分析上下文</p>
        </div>
        <div className="space-y-3">
          {sessions.map(s => {
            const ctx = s.context_summary ? (() => { try { return JSON.parse(s.context_summary) } catch { return null } })() : null
            return (
              <div key={s.session_id} className="bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card transition-shadow cursor-pointer"
                onClick={() => router.push(`/agent?session=${s.session_id}`)}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 size={14} className="text-green-500" />
                    <span className="text-sm text-text-primary">{s.title}</span>
                  </div>
                  <span className="text-xs text-text-muted">{s.updated_at?.slice(0, 10)}</span>
                </div>
                <div className="flex items-center gap-3 text-xs text-text-muted">
                  <span>{s.message_count} 条消息</span>
                  {ctx && (
                    <div className="flex items-center gap-2 ml-auto">
                      {ctx.sql_results ? <span className="flex items-center gap-0.5"><Brain size={10} /> SQL×{ctx.sql_results}</span> : null}
                      {ctx.rag_docs ? <span>RAG×{ctx.rag_docs}</span> : null}
                      {ctx.turns ? <span>{ctx.turns} 轮</span> : null}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
