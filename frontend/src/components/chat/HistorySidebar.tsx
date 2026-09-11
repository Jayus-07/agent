'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import type { SessionMeta } from '@/lib/api/memory'
import { getSessionsCached, invalidateSessionsCache } from '@/lib/sessions-cache'
import { deleteMemorySession, renameMemorySession } from '@/lib/api/memory'
import { parseContextSummary } from '@/lib/context-summary'
import {
  PanelRightClose, Brain, MessageSquare, Plus, RefreshCw, Search, Pencil, Trash2,
} from 'lucide-react'

// ── 时间分组工具 ──

type TimeBucket = 'today' | 'yesterday' | 'week' | 'older'

const BUCKET_LABELS: Record<TimeBucket, string> = {
  today: '今天',
  yesterday: '昨天',
  week: '最近 7 天',
  older: '更早',
}

function bucketOf(iso: string | null | undefined): TimeBucket {
  if (!iso) return 'older'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'older'
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const t = d.getTime()
  if (t >= startOfToday) return 'today'
  if (t >= startOfToday - 86_400_000) return 'yesterday'
  if (t >= startOfToday - 7 * 86_400_000) return 'week'
  return 'older'
}

/** 紧凑时间显示：今天显示时刻，更早显示日期 */
function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  if (d.toDateString() === now.toDateString()) {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  return `${d.getMonth() + 1}/${d.getDate()}`
}

const BUCKET_ORDER: TimeBucket[] = ['today', 'yesterday', 'week', 'older']

function groupByTime(sessions: SessionMeta[]): { bucket: TimeBucket; items: SessionMeta[] }[] {
  const map = new Map<TimeBucket, SessionMeta[]>()
  for (const s of sessions) {
    const b = bucketOf(s.updated_at)
    if (!map.has(b)) map.set(b, [])
    map.get(b)!.push(s)
  }
  return BUCKET_ORDER.filter((b) => map.has(b)).map((bucket) => ({ bucket, items: map.get(bucket)! }))
}

// ── 单条会话行 ──

interface RowProps {
  session: SessionMeta
  isActive: boolean
  onSelect: () => void
  onRename: (title: string) => void
  onDelete: () => void
  activeRef?: (el: HTMLButtonElement | null) => void
}

function SessionRow({ session: s, isActive, onSelect, onRename, onDelete, activeRef }: RowProps) {
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
          className={`w-full text-left px-3 py-2 pr-14 rounded-lg transition-colors ${
            isActive ? 'text-accent' : 'text-text-secondary'
          }`}
        >
          <div className="text-[13px] font-medium truncate">{s.title}</div>
          <div className="flex items-center gap-2 mt-1 text-[10px] text-text-muted">
            <span className="flex items-center gap-1">
              <MessageSquare size={10} /> {s.message_count}
            </span>
            {ctx?.turns ? <span>{ctx.turns} 轮</span> : null}
            <span className="ml-auto">{formatTime(s.updated_at)}</span>
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

// ── 侧栏主体 ──

export default function HistorySidebar({ onClose }: { onClose: () => void }) {
  // P1-12 / P1-16：sessions 走 sessions-cache dedup，historyError 也只在这里展示
  const historyError = useChatStore((s) => s.historyError)
  const sessionsVersion = useChatStore((s) => s.sessionsVersion)
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')
  const currentId = useChatStore((s) => s.currentId)
  const router = useRouter()
  const activeRowRef = useRef<HTMLButtonElement | null>(null)

  const refresh = useCallback(async (force: boolean) => {
    if (force) setRefreshing(true)
    try {
      const data = await getSessionsCached(force)
      setSessions(data)
      setLoadError(null)
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : '加载历史失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    refresh(false)
  }, [refresh])

  // 会话结束（SSE done → sessionsVersion 自增）后自动刷新列表。
  // 延迟 1.2s：后端在 done 事件后才提交 end_turn 持久化，立刻刷会拿不到新会话
  useEffect(() => {
    // 首次挂载（version=0）走上面的初始加载，且首次渲染不触发刷新
    if (sessionsVersion === 0) return
    const timer = setTimeout(() => refresh(true), 1200)
    return () => clearTimeout(timer)
  }, [sessionsVersion, refresh])

  // 当前会话滚动到可视区域
  useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: 'nearest' })
  }, [currentId, sessions])

  const handleNewChat = () => {
    useChatStore.getState().newSession()
    router.push('/agent')
  }

  const handleSelect = (sid: string) => {
    router.push(`/agent?session=${sid}`)
  }

  const handleRename = async (sid: string, title: string) => {
    try {
      await renameMemorySession(sid, title)
    } catch {
      // 重命名失败不阻塞 UI，刷新后恢复原标题
    } finally {
      invalidateSessionsCache()
      refresh(true)
      if (sid === currentId) useChatStore.getState().renameSession(sid, title)
    }
  }

  const handleDelete = async (sid: string) => {
    if (!window.confirm('确定删除这条会话记录吗？删除后不可恢复。')) return
    try {
      await deleteMemorySession(sid)
    } catch {
      // 删除失败（如 404）时同样刷新，让列表与后端对齐
    } finally {
      invalidateSessionsCache()
      refresh(true)
      if (sid === currentId) {
        useChatStore.getState().newSession()
        router.push('/agent')
      }
    }
  }

  const errorMsg = loadError

  // 搜索过滤（标题匹配，大小写不敏感）
  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    if (!kw) return sessions
    return sessions.filter((s) => s.title?.toLowerCase().includes(kw))
  }, [sessions, keyword])

  const groups = useMemo(() => groupByTime(filtered), [filtered])

  return (
    <aside className="hidden md:flex w-64 shrink-0 flex-col glass border-l border-black/5">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-text-primary">
          <Brain size={14} className="text-accent" />
          分析历史
        </div>
        <div className="flex items-center gap-0.5">
          <button
            onClick={handleNewChat}
            className="p-1.5 rounded hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
            aria-label="新对话"
            title="新对话"
          >
            <Plus size={15} />
          </button>
          <button
            onClick={() => refresh(true)}
            className="p-1.5 rounded hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
            aria-label="刷新"
            title="刷新"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-black/5 text-text-muted transition-colors"
            aria-label="关闭"
            title="关闭"
          >
            <PanelRightClose size={15} />
          </button>
        </div>
      </div>

      {/* 搜索框 */}
      <div className="px-3 pb-2">
        <div className="relative">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索会话…"
            className="w-full bg-black/[0.04] border border-black/5 rounded-lg pl-7 pr-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted outline-none focus:border-accent/40 transition-colors"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {loading ? (
          <p className="text-xs text-text-muted px-2 py-6 text-center">加载中...</p>
        ) : errorMsg ? (
          <p className="text-xs text-red-500 px-2 py-6 text-center break-words">
            历史加载失败
            <span className="block mt-1 text-[10px] text-text-muted">{errorMsg}</span>
          </p>
        ) : filtered.length === 0 ? (
          <p className="text-xs text-text-muted px-2 py-6 text-center">
            {keyword ? '没有匹配的会话' : '暂无分析记录'}
          </p>
        ) : (
          groups.map(({ bucket, items }) => (
            <div key={bucket} className="mb-2">
              <div className="px-2 py-1 text-[10px] font-medium text-text-muted/80 uppercase tracking-wide">
                {BUCKET_LABELS[bucket]}
              </div>
              <div className="space-y-0.5">
                {items.map((s) => (
                  <SessionRow
                    key={s.session_id}
                    session={s}
                    isActive={s.session_id === currentId}
                    onSelect={() => handleSelect(s.session_id)}
                    onRename={(title) => handleRename(s.session_id, title)}
                    onDelete={() => handleDelete(s.session_id)}
                    activeRef={(el) => {
                      if (s.session_id === currentId) activeRowRef.current = el
                    }}
                  />
                ))}
              </div>
            </div>
          ))
        )}
        {/* historyError 兜底 banner（删除/重命名后可能引发） */}
        {historyError && !loadError && (
          <p className="text-[10px] text-amber-600 px-2 py-1 mt-2">提示：{historyError}</p>
        )}
      </div>
    </aside>
  )
}
