'use client'

/**
 * SessionList — 任务栏里的会话列表（分组渲染 + 四态处理）
 *
 * 职责：
 *  - 封装 sessions-cache 拉取与 sessionsVersion 自动刷新（会话结束 1.2s 后重拉）
 *  - 分组渲染（今天 / 昨天 / 最近 7 天 / 更早）、空态、加载态、错误态、historyError banner
 *  - 会话选中 / 重命名 / 删除（删除与重命名后失效缓存并强制刷新）
 *
 * 搜索关键字由外部（TaskSidebar）持有并通过 keyword 传入，避免两处各存一份。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import type { SessionMeta } from '@/lib/api/memory'
import { deleteMemorySession, renameMemorySession } from '@/lib/api/memory'
import { getSessionsCached, invalidateSessionsCache } from '@/lib/sessions-cache'
import { BUCKET_LABELS, filterByKeyword, groupByTime } from '@/lib/session-groups'
import SessionRow from './SessionRow'

interface Props {
  /** 标题搜索关键字（由父组件持有） */
  keyword?: string
  /** 自增触发一次强制刷新（父组件的刷新按钮用） */
  refreshKey?: number
  /** 刷新中状态外抛，供父组件让刷新图标转起来 */
  onRefreshingChange?: (refreshing: boolean) => void
  /** 空态里的引导动作（通常是「新建任务」） */
  onEmptyAction?: () => void
}

export default function SessionList({ keyword = '', refreshKey = 0, onRefreshingChange, onEmptyAction }: Props) {
  const historyError = useChatStore((s) => s.historyError)
  const sessionsVersion = useChatStore((s) => s.sessionsVersion)
  const currentId = useChatStore((s) => s.currentId)
  const router = useRouter()

  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  // 回调放 ref，避免父组件传内联函数时把 refresh 的引用打穿（导致重复拉取）
  const refreshingCbRef = useRef(onRefreshingChange)
  useEffect(() => { refreshingCbRef.current = onRefreshingChange }, [onRefreshingChange])

  const refresh = useCallback(async (force: boolean) => {
    if (force) {
      setRefreshing(true)
      refreshingCbRef.current?.(true)
    }
    try {
      const data = await getSessionsCached(force)
      setSessions(data)
      setLoadError(null)
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : '加载历史失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
      refreshingCbRef.current?.(false)
    }
  }, [])

  // 首次挂载：走缓存（10s TTL / 并发 dedup）
  useEffect(() => { refresh(false) }, [refresh])

  // 会话结束（SSE done → sessionsVersion 自增）后自动刷新。
  // 延迟 1.2s：后端在 done 事件后才提交 end_turn 持久化，立刻刷会拿不到新会话。
  useEffect(() => {
    if (sessionsVersion === 0) return
    const timer = setTimeout(() => refresh(true), 1200)
    return () => clearTimeout(timer)
  }, [sessionsVersion, refresh])

  // 外部刷新信号（refreshKey 从 0 起，0 表示未触发）
  useEffect(() => {
    if (!refreshKey) return
    refresh(true)
  }, [refreshKey, refresh])

  const activeRowRef = useRef<HTMLButtonElement | null>(null)

  // 当前会话滚动到可视区域
  useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: 'nearest' })
  }, [currentId, sessions])

  const handleSelect = (sid: string) => {
    if (sid === currentId) return
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

  const filtered = useMemo(() => filterByKeyword(sessions, keyword), [sessions, keyword])
  const groups = useMemo(() => groupByTime(filtered), [filtered])

  if (loading) {
    return <p className="text-xs text-text-muted px-2 py-6 text-center">加载中...</p>
  }

  if (loadError) {
    return (
      <p className="text-xs text-red-500 px-2 py-6 text-center break-words">
        历史加载失败
        <span className="block mt-1 text-[10px] text-text-muted">{loadError}</span>
      </p>
    )
  }

  if (filtered.length === 0) {
    return (
      <div className="px-2 py-6 text-center">
        <p className="text-xs text-text-muted">{keyword ? '没有匹配的任务' : '暂无任务记录'}</p>
        {!keyword && onEmptyAction && (
          <button
            onClick={onEmptyAction}
            className="mt-2 text-[11px] text-accent hover:underline"
          >
            新建第一个任务
          </button>
        )}
      </div>
    )
  }

  return (
    <div>
      {groups.map(({ bucket, items }) => (
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
      ))}
      {/* historyError 兜底 banner（删除/重命名后可能引发） */}
      {historyError && (
        <p className="text-[10px] text-amber-600 px-2 py-1 mt-2">提示：{historyError}</p>
      )}
    </div>
  )
}
