'use client'

import { useEffect, useState } from 'react'
import { Brain, Database, FileText, FileSpreadsheet } from 'lucide-react'

interface ContextData {
  sql_results?: number; rag_docs?: number; last_report?: string; turns?: number
}

export default function ContextPanel({ sessionId }: { sessionId: string }) {
  const [ctx, setCtx] = useState<ContextData | null>(null)

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    // 辅助信息条：失败时不占用界面提示（对话本身不受影响），
    // 但要校验 res.ok，避免把 4xx/5xx 的错误响应体当成上下文数据
    fetch(`/api/memory/sessions/${encodeURIComponent(sessionId)}/context`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(d => { if (!cancelled && d.context) setCtx(d.context) })
      .catch((e: unknown) => {
        console.warn('[ContextPanel] 上下文加载失败:', e)
        if (!cancelled) setCtx(null)
      })
    return () => { cancelled = true }
  }, [sessionId])

  if (!ctx) return null

  return (
    <div className="shrink-0 border-b border-border-subtle bg-accent/3 px-4 py-2">
      <div className="max-w-[720px] mx-auto flex items-center gap-3 text-xs">
        <Brain size={13} className="text-accent" />
        <span className="text-text-muted">上下文:</span>
        {ctx.sql_results ? <span className="flex items-center gap-1 text-text-secondary"><Database size={11} /> SQL×{ctx.sql_results}</span> : null}
        {ctx.rag_docs ? <span className="flex items-center gap-1 text-text-secondary"><FileText size={11} /> RAG×{ctx.rag_docs}</span> : null}
        {ctx.last_report ? <span className="flex items-center gap-1 text-text-secondary"><FileSpreadsheet size={11} /> {ctx.last_report}</span> : null}
        {ctx.turns ? <span className="text-text-muted ml-auto">{ctx.turns} 轮</span> : null}
      </div>
    </div>
  )
}
