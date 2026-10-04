'use client'

/**
 * CandidatesPanel — 分类候选表（验收 #10，2026-10-05）
 *
 * 旅游页出单后的候选池浏览区：景点/美食/酒店三类 tab（空组隐藏——交通
 * 无候选数据、酒店暂无生产者，不伪造 tab）+ 列表 + 「换入」。
 *
 * - 数据：GET /api/travel/candidates（后端读域图 checkpoint 的候选池，
 *   随重排失效；plan_version 随响应带出做旧版标注，#102 口径）。
 * - 换入：onAskReplace 回调交给页面走既有代发草案管线（decision=
 *   canvas_replace 留痕 + chatRef.send 代发），本组件零新管线。
 * - 诚实口径：available=false 显示后端 hint（会话状态过期），不伪造候选。
 */
import { useEffect, useMemo, useState } from 'react'
import { ListChecks, Star } from 'lucide-react'
import { fetchTravelCandidates, type TravelCandidate } from '@/api/travel'

const TAB_ORDER = ['景点', '美食', '酒店'] as const

/** 来源标签（与行程单 source 溯源同口径的轻量投影）。 */
function sourceLabel(source: string): string {
  if (source.startsWith('amap')) return '高德'
  if (source.startsWith('tencent')) return '腾讯'
  if (source.startsWith('rag')) return '本地攻略'
  if (source.startsWith('zhihu')) return '知乎提及'
  if (source.startsWith('seed')) return '本地数据'
  return source || '未知'
}

export default function CandidatesPanel({
  conversationId,
  planVersion,
  generating = false,
  activeDay = 1,
  onAskReplace,
}: {
  conversationId: string
  /** 当前行程版本（换入 payload 与旧版标注用） */
  planVersion: number
  /** 草案生成中：换入按钮置灰（同一条管线正在跑，防双发） */
  generating?: boolean
  /** 当前选中天（换入的目标天，随主视图 DayTab 联动） */
  activeDay?: number
  onAskReplace?: (candidate: TravelCandidate, targetDay: number) => void
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchTravelCandidates>> | null>(null)
  const [failed, setFailed] = useState(false)
  const [tab, setTab] = useState<string>('')
  // planVersion 变化（重排/应用草案）→ 候选池随之失效，重拉
  useEffect(() => {
    if (!conversationId) return
    let alive = true
    setFailed(false)
    fetchTravelCandidates(conversationId)
      .then((resp) => {
        if (!alive) return
        setData(resp)
        const firstNonEmpty = TAB_ORDER.find((g) => (resp.groups[g]?.length ?? 0) > 0)
        setTab(firstNonEmpty ?? '')
      })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [conversationId, planVersion])

  const tabs = useMemo(() => {
    if (!data) return [] as Array<{ name: string; count: number }>
    return TAB_ORDER.filter((g) => (data.groups[g]?.length ?? 0) > 0)
      .map((g) => ({ name: g, count: data.groups[g].length }))
  }, [data])

  const items = data?.groups[tab] ?? []

  return (
    <section
      aria-label="分类候选"
      className="rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card"
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <ListChecks size={15} aria-hidden className="text-[#087b73]" />
        <h3 className="text-sm font-semibold text-[#183037]">分类候选</h3>
        {data && data.plan_version > 0 && (
          <span className="rounded bg-[#f5faf9] px-1.5 py-0.5 text-[10px] text-[#5c7074]">
            v{data.plan_version}
          </span>
        )}
        {data && planVersion > 0 && data.plan_version < planVersion && (
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">
            来自旧版 v{data.plan_version}（当前 v{planVersion}）
          </span>
        )}
        <span className="text-[11px] text-[#8aa0a4]">点「换入」由助手改排行程</span>
      </header>

      {failed && (
        <p className="mt-2 text-[11px] text-[#8aa0a4]">候选列表加载失败，可稍后重试。</p>
      )}
      {data && !data.available && (
        <p className="mt-2 text-[11px] text-[#8aa0a4]">{data.hint || '候选池暂不可用'}</p>
      )}
      {data && data.available && tabs.length === 0 && (
        <p className="mt-2 text-[11px] text-[#8aa0a4]">本会话暂无候选数据。</p>
      )}

      {tabs.length > 0 && (
        <>
          <div className="mt-3 flex gap-1.5" role="tablist" aria-label="候选分类">
            {tabs.map((t) => (
              <button
                key={t.name}
                type="button"
                role="tab"
                aria-selected={tab === t.name}
                onClick={() => setTab(t.name)}
                className={`rounded-full px-3 py-1 text-[11px] transition-colors ${
                  tab === t.name
                    ? 'bg-[#087b73] text-white'
                    : 'bg-[#f5faf9] text-[#5c7074] hover:bg-[#e2f0ee]'
                }`}
              >
                {t.name} {t.count}
              </button>
            ))}
          </div>

          <ul className="mt-2 divide-y divide-[#eef4f3]">
            {items.map((c) => (
              <li key={c.poi_id || c.name} className="flex items-center gap-2 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium text-[#183037]">
                    {c.name}
                    {c.rating > 0 && (
                      <span className="ml-1.5 inline-flex items-center gap-0.5 text-[11px] text-amber-600">
                        <Star size={10} aria-hidden />{c.rating.toFixed(1)}
                      </span>
                    )}
                  </p>
                  <p className="truncate text-[11px] text-[#8aa0a4]">
                    {sourceLabel(c.source)}
                    {c.reason ? ` · ${c.reason}` : ''}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={generating || !onAskReplace}
                  onClick={() => onAskReplace?.(c, activeDay)}
                  className="shrink-0 rounded-full border border-[#087b73] px-2.5 py-1 text-[11px] font-medium text-[#087b73] transition-colors hover:bg-[#e2f0ee] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  换入
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
