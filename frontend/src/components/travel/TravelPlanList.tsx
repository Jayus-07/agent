'use client'

/**
 * TravelPlanList — 任务侧栏（travel 模式）下半区的历史规划列表
 *
 * 与 /agent 的 SessionList 同构（TaskSidebar 复用的另一半）：
 *  - 数据源 = GET /api/travel/plans（按用户聚合 travel_plan_versions，每会话最新版）
 *  - 分组/过滤/相对时间 = lib/session-groups（与对话页同一套工具，口径不漂移）
 *  - 行视觉 = SessionRow 同款（标题截断 + 右侧相对时间 + 选中高亮），无重命名/删除
 *    （后端没有对应端点，不做假功能）
 *  - 点击 = 恢复该规划到画布（onRestore，由页面切 conversation_id + 回填表单），
 *    不走路由跳转 —— 旅游页是单画布，没有「切换会话页」的语义
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, MapPin } from 'lucide-react'
import { fetchTravelPlanList, type TravelPlanSummary } from '@/api/travel'
import { BUCKET_LABELS, bucketOf, formatTime } from '@/lib/session-groups'
import type { TimeBucket } from '@/lib/session-groups'

interface Props {
  /** 标题搜索关键字（TaskSidebar 持有并下传，同 SessionList 口径） */
  keyword?: string
  /** 自增触发一次强制刷新（侧栏刷新按钮 / 出单后自动刷新） */
  refreshKey?: number
  /** 刷新中状态外抛（侧栏刷新图标转起来） */
  onRefreshingChange?: (refreshing: boolean) => void
  /** 空态引导动作（新建规划） */
  onEmptyAction?: () => void
  /** 当前画布上的会话（高亮「当前」） */
  currentId?: string
  /** 正在恢复中的会话（行内转圈） */
  restoringCid?: string
  onRestore: (cid: string) => void
}

export default function TravelPlanList({
  keyword = '', refreshKey = 0, onRefreshingChange, onEmptyAction,
  currentId = '', restoringCid = '', onRestore,
}: Props) {
  const [plans, setPlans] = useState<TravelPlanSummary[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const refreshingCbRef = useRef(onRefreshingChange)
  useEffect(() => { refreshingCbRef.current = onRefreshingChange }, [onRefreshingChange])

  const refresh = useCallback(async (force: boolean) => {
    if (force) refreshingCbRef.current?.(true)
    try {
      const data = await fetchTravelPlanList()
      setPlans(data)
      setLoadError(null)
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : '加载历史规划失败')
    } finally {
      setLoading(false)
      refreshingCbRef.current?.(false)
    }
  }, [])

  // 首次挂载 + 外部刷新信号（出单后 / 刷新按钮）
  useEffect(() => { refresh(refreshKey > 0) }, [refresh, refreshKey])

  const filtered = useMemo(() => {
    const mapped = (plans ?? []).map((p) => ({
      plan: p,
      title: p.destination || '未命名规划',
      createdAt: p.created_at,
    }))
    const kw = keyword.trim().toLowerCase()
    if (!kw) return mapped
    return mapped.filter((m) => m.title.toLowerCase().includes(kw))
  }, [plans, keyword])

  // 按「今天 / 昨天 / 最近 7 天 / 更早」分桶（桶内保持后端的新→旧序）
  const groups = useMemo(() => {
    const map = new Map<TimeBucket, typeof filtered>()
    for (const item of filtered) {
      const b = bucketOf(item.createdAt)
      if (!map.has(b)) map.set(b, [])
      map.get(b)!.push(item)
    }
    return [...map.entries()]
  }, [filtered])

  if (loading && !plans) {
    return <p className="text-xs text-text-muted px-2 py-6 text-center">加载中...</p>
  }

  if (loadError) {
    return (
      <p className="text-xs text-red-500 px-2 py-6 text-center break-words">
        历史规划加载失败
        <span className="block mt-1 text-[10px] text-text-muted">{loadError}</span>
      </p>
    )
  }

  if (filtered.length === 0) {
    return (
      <div className="px-2 py-6 text-center">
        <p className="text-xs text-text-muted">{keyword ? '没有匹配的规划' : '暂无历史规划'}</p>
        {!keyword && onEmptyAction && (
          <button onClick={onEmptyAction} className="mt-2 text-[11px] text-accent hover:underline">
            新建第一份规划
          </button>
        )}
      </div>
    )
  }

  return (
    <div>
      <div className="px-2 pt-1 pb-1 text-[10px] font-medium text-text-muted/80 uppercase tracking-wide">
        历史规划 ({filtered.length})
      </div>
      {groups.map(([bucket, items]) => (
        <div key={bucket} className="mb-2">
          <div className="px-2 py-1 text-[10px] font-medium text-text-muted/80 uppercase tracking-wide">
            {BUCKET_LABELS[bucket]}
          </div>
          <div className="space-y-0.5">
            {items.map(({ plan: p, title }) => {
              const isCurrent = p.conversation_id === currentId
              const restoring = p.conversation_id === restoringCid
              return (
                <div
                  key={p.conversation_id}
                  className={`group relative rounded-lg transition-colors ${
                    isCurrent ? 'bg-accent-soft' : 'hover:bg-black/5'
                  }`}
                >
                  <button
                    onClick={() => onRestore(p.conversation_id)}
                    disabled={!!restoringCid}
                    title={`${title} · v${p.plan_version}`}
                    className={`w-full text-left px-3 py-1.5 pr-14 rounded-lg transition-colors ${
                      isCurrent ? 'text-accent' : 'text-text-secondary'
                    } disabled:opacity-60`}
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="flex-1 min-w-0 text-[13px] font-medium truncate inline-flex items-center gap-1">
                        <MapPin size={11} className="shrink-0 opacity-60" aria-hidden />
                        {title}
                      </span>
                      <span className="shrink-0 text-[10px] text-text-muted">
                        {restoring ? (
                          <span className="flex items-center gap-1 text-accent" title="恢复中">
                            <Loader2 size={10} className="animate-spin" />
                            恢复中
                          </span>
                        ) : (
                          formatTime(p.created_at)
                        )}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[10px]">
                      <span
                        className={
                          p.plan_status === 'confirmed' ? 'text-green-600' : 'text-amber-600'
                        }
                      >
                        {p.plan_status === 'confirmed' ? '已确认' : '待确认'}
                      </span>
                      <span className="text-text-muted">v{p.plan_version}</span>
                      {isCurrent && <span className="text-accent">当前</span>}
                    </div>
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
