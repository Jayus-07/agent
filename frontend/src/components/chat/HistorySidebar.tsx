'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import type { SessionMeta } from '@/lib/api/memory'
import { getSessionsCached } from '@/lib/sessions-cache'
import { parseContextSummary } from '@/lib/context-summary'
import { PanelRightClose, Brain, MessageSquare } from 'lucide-react'

export default function HistorySidebar({ onClose }: { onClose: () => void }) {
  // P1-12 / P1-16：sessions 走 sessions-cache dedup，historyError 也只在这里展示
  // （避免与 tasks 页面同时拉同一接口，以及 ChatView 主区出现两个错误提示）
  const historyError = useChatStore((s) => s.historyError)
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const currentId = useChatStore((s) => s.currentId)
  const router = useRouter()

  useEffect(() => {
    let cancelled = false
    getSessionsCached()
      .then((data) => {
        if (!cancelled) {
          setSessions(data)
          setLoadError(null)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : '加载历史失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleSelect = (sid: string) => {
    router.push(`/agent?session=${sid}`)
  }

  // 优先展示 sessions-cache 的独立错误；store.historyError 暂作为兜底 banner
  const errorMsg = loadError

  return (
    <aside className="w-64 shrink-0 flex flex-col glass border-l border-black/5">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-text-primary">
          <Brain size={14} className="text-accent" />
          分析历史
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-black/5 text-text-muted transition-colors"
          aria-label="关闭"
        >
          <PanelRightClose size={15} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {loading ? (
          <p className="text-xs text-text-muted px-2 py-6 text-center">加载中...</p>
        ) : errorMsg ? (
          <p className="text-xs text-red-500 px-2 py-6 text-center break-words">
            历史加载失败
            <span className="block mt-1 text-[10px] text-text-muted">{errorMsg}</span>
          </p>
        ) : sessions.length === 0 ? (
          <p className="text-xs text-text-muted px-2 py-6 text-center">暂无分析记录</p>
        ) : (
          <div className="space-y-0.5">
            {sessions.map((s) => {
              const isActive = s.session_id === currentId
              const ctx = parseContextSummary(s.context_summary)
              return (
                <button
                  key={s.session_id}
                  onClick={() => handleSelect(s.session_id)}
                  className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                    isActive ? 'bg-accent/8 text-accent' : 'hover:bg-black/5 text-text-secondary'
                  }`}
                >
                  <div className="text-[13px] font-medium truncate">{s.title}</div>
                  <div className="flex items-center gap-2 mt-1 text-[10px] text-text-muted">
                    <span className="flex items-center gap-1">
                      <MessageSquare size={10} /> {s.message_count}
                    </span>
                    {ctx?.turns ? <span>{ctx.turns} 轮</span> : null}
                    <span className="ml-auto">{s.updated_at?.slice(0, 10)}</span>
                  </div>
                  {ctx && (
                    <div className="flex items-center gap-1.5 mt-1 text-[10px]">
                      {ctx.sql_results ? (
                        <span className="text-accent">SQL×{ctx.sql_results}</span>
                      ) : null}
                      {ctx.rag_docs ? (
                        <span className="text-green-500">RAG×{ctx.rag_docs}</span>
                      ) : null}
                    </div>
                  )}
                </button>
              )
            })}
          </div>
        )}
        {/* historyError 兜底 banner（删除/重命名后可能引发） */}
        {historyError && !loadError && (
          <p className="text-[10px] text-amber-600 px-2 py-1 mt-2">提示：{historyError}</p>
        )}
      </div>
    </aside>
  )
}
