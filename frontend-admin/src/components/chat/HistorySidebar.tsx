'use client'

/**
 * HistorySidebar — 右侧会话历史栏（保留供回滚）
 *
 * 分组/时间显示/SessionRow 已抽到 lib/session-groups.ts 与 components/agent/SessionRow.tsx，
 * 与 TaskSidebar 共用同一套实现，避免两处口径漂移。
 * 当前 /agent 已切到左侧 TaskSidebar，本组件不在页面中挂载。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import type { SessionMeta } from '@/api/memory'
import { getSessionsCached, invalidateSessionsCache } from '@/lib/sessions-cache'
import { deleteMemorySession, renameMemorySession } from '@/api/memory'
import { BUCKET_LABELS, filterByKeyword, groupByTime } from '@/lib/session-groups'
import SessionRow from '@/components/agent/SessionRow'
import {
  PanelRightClose, Brain, Plus, RefreshCw, Search, User,
} from 'lucide-react'

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

  const filtered = useMemo(() => filterByKeyword(sessions, keyword), [sessions, keyword])
  const groups = useMemo(() => groupByTime(filtered), [filtered])

  return (
    <aside className="hidden md:flex w-[260px] shrink-0 flex-col bg-sidebar border-l border-black/5">
      {/* Logo 区：品牌展示 + 刷新/收起（控制台全局导航在根布局左侧） */}
      <div className="flex items-center gap-2 px-4 h-12 shrink-0">
        <Brain size={18} className="text-accent shrink-0" />
        <span className="text-sm font-semibold text-text-primary">Agent AI</span>
        <div className="ml-auto flex items-center gap-0.5">
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
            aria-label="收起历史栏"
            title="收起历史栏"
          >
            <PanelRightClose size={15} />
          </button>
        </div>
      </div>

      {/* 搜索框 */}
      <div className="px-3 pb-2 shrink-0">
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

      {/* 显眼的新对话入口 */}
      <div className="px-3 pb-2 shrink-0">
        <button
          onClick={handleNewChat}
          className="w-full flex items-center justify-center gap-1.5 rounded-full border border-black/10
            bg-white px-3 py-2 text-[13px] font-medium text-text-primary shadow-sm
            hover:bg-black/[0.03] hover:shadow-card active:scale-[0.99]
            transition-all duration-200"
        >
          <Plus size={14} />
          开启新对话
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

      {/* 底部固定用户信息 */}
      <div className="shrink-0 border-t border-black/5 px-3 py-3 flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center shrink-0">
          <User size={15} className="text-accent" />
        </div>
        <div className="min-w-0">
          <div className="text-[13px] font-medium text-text-primary truncate">本地用户</div>
          <div className="text-[10px] text-text-muted truncate">电商 RAG 工作台</div>
        </div>
      </div>
    </aside>
  )
}
