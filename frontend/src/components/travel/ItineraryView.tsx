'use client'

/**
 * ItineraryView — 行程结果展示（2026-10-01 三栏方案版）
 *
 * 视觉基准 = 用户提供的参考 HTML（旅游规划页面交互示意）：青绿主色
 * #087b73、软底面板 #f5faf9、描边 #dae7e5、墨色文字 #183037、圆角卡片、
 * 按天 Tab（tp-day）、阶段 chips。这些色值**只在本页使用**（页面级主题，
 * 不动全局 design tokens——全局 accent 仍是蓝，改全局要走三端同步纪律）。
 *
 * 结构自上而下：概览（标题+版本徽章+倒计时 / 预算合计+占比条+图例）→
 * 路线地图（**按天分色连线 + 到访序号**，客户端 Web Mercator 投影，与
 * 底图同参数投影保证对齐；连线是「按到访顺序的示意」，不是实际道路——
 * 方案 v2 的不伪造原则）→ 按天 Tab + 单日时间轴 → 出行须知（折叠）→ 文本版。
 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import 'leaflet/dist/leaflet.css'
import type { Map as LeafletMap } from 'leaflet'
import {
  AlertTriangle, BedDouble, CalendarPlus, Check, Coffee, History, ListChecks, MapPin, Route,
  ThumbsDown, ThumbsUp, TrainFront, Ticket, Undo2, UtensilsCrossed, Wallet,
} from 'lucide-react'
import {
  confirmTravelPlan, fetchTravelPlanDiff, fetchTravelPlanVersions, restoreTravelPlan,
  type Itinerary, type ItineraryDay, type ItineraryItem, type PlanResponse,
  type TransitLeg, type TravelPlanDiff, type TravelPlanVersion,
} from '@/api/travel'
import { formatDayDate, PACE_LABEL } from './planState'
import {
  costBreakdown, dayLoad, dayMealItems, dayRouteColor, dayVisitTitles, departureBadge, formatDuration,
} from './travelDisplay'
import MarkdownContent from '@/components/chat/MarkdownContent'
import { classifyFact, costAvailability } from './travelRuntime'

// ── 页面级主题（来源：参考 HTML #trip-concept 的 CSS 变量，勿当全局 token 用） ──
const TP = {
  accent: '#087b73',
  soft: '#f5faf9',
  line: '#dae7e5',
  ink: '#183037',
  muted: '#5c7074',
} as const

// ── 路线图（Leaflet 交互地图：滚轮缩放 / 拖拽平移，fitBounds 按行程自适应） ──

interface DayRoute {
  dayIndex: number
  pts: Array<{ lat: number; lng: number; title: string }>
}

function RouteMap({ itinerary, selectedDay }: { itinerary: Itinerary; selectedDay: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<LeafletMap | null>(null)
  const leafletRef = useRef<typeof import('leaflet') | null>(null)
  const layerRef = useRef<import('leaflet').LayerGroup | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  // 每天的到访点（kind=visit 且有坐标），按当日时间顺序
  const dayRoutes = useMemo<DayRoute[]>(() => {
    const routes: DayRoute[] = []
    for (const d of itinerary.days.filter((day) => day.day_index === selectedDay)) {
      const pts = d.items
        .filter((item): item is ItineraryItem & { poi: NonNullable<ItineraryItem['poi']> } =>
          item.kind === 'visit'
            && item.poi != null
            && classifyFact({ source: item.poi.source, verification_status: item.poi.verification_status }) === 'verified')
        .map((item) => ({ lat: item.poi.lat, lng: item.poi.lng, title: item.title }))
      if (pts.length > 0) routes.push({ dayIndex: d.day_index, pts })
    }
    return routes
  }, [itinerary, selectedDay])

  const selectedDayData = itinerary.days.find((day) => day.day_index === selectedDay)
  const missingLocation = Boolean(selectedDayData?.items.some((item) => item.kind === 'visit' && (
    !item.poi
      || !Number.isFinite(item.poi.lat)
      || !Number.isFinite(item.poi.lng)
      || classifyFact({ source: item.poi.source, verification_status: item.poi.verification_status }) !== 'verified'
  )))

  // 建图（一次）：瓦片走高德（GCJ-02，与种子/静态图坐标同口径），无 Key 前端直连
  useEffect(() => {
    let cancelled = false
    let map: LeafletMap | null = null
    ;(async () => {
      try {
        const L = await import('leaflet')
        if (cancelled || !containerRef.current) return
        map = L.map(containerRef.current, {
          zoomControl: true,
          scrollWheelZoom: true, // 滚轮缩放（用户要求）；容器内滚动互不干扰见 CSS touch-action
          attributionControl: true,
        })
        L.tileLayer(
          'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
          { subdomains: '1234', maxZoom: 18, attribution: '© 高德地图' },
        ).addTo(map)
        map.attributionControl.setPrefix(false)
        leafletRef.current = L
        mapRef.current = map
        setReady(true)
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
      map?.remove()
      mapRef.current = null
      layerRef.current = null
    }
  }, [])

  // 画层（行程变化即重画 + 自适应取景）
  useEffect(() => {
    const map = mapRef.current
    const L = leafletRef.current
    if (!ready || !map || !L) return
    layerRef.current?.remove()
    const group = L.layerGroup()
    const bounds: [number, number][] = []
    let seq = 0
    for (const route of dayRoutes) {
      const color = dayRouteColor(route.dayIndex)
      if (route.pts.length >= 2) {
        // 按到访顺序的示意连线（虚线），不是实际道路——诚实口径见 figcaption
        L.polyline(
          route.pts.map((p) => [p.lat, p.lng] as [number, number]),
          { color, weight: 3, dashArray: '8 6', opacity: 0.85 },
        ).addTo(group)
      }
      for (const p of route.pts) {
        seq += 1
        const icon = L.divIcon({
          className: 'travel-seq-marker',
          html: `<span style="display:flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:9999px;background:${color};color:#fff;font:700 11px/16px system-ui;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)">${seq}</span>`,
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        })
        L.marker([p.lat, p.lng], { icon, title: p.title }).addTo(group)
        bounds.push([p.lat, p.lng])
      }
    }
    group.addTo(map)
    layerRef.current = group
    if (bounds.length > 0) {
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 })
    }
  }, [ready, dayRoutes])

  if (failed || dayRoutes.length === 0) {
    return (
      <figure className="animate-fade-in overflow-hidden rounded-2xl border border-red-200 bg-white shadow-card">
        <figcaption className="border-b border-red-100 px-4 py-2 text-xs font-medium text-[#183037]">
          <Route size={13} className="mr-1 inline text-[#087b73]" aria-hidden />第 {selectedDay} 天地图
        </figcaption>
        <p className="px-4 py-8 text-center text-sm text-red-600">
          {failed ? '地图暂时无法加载' : `第 ${selectedDay} 天暂无可用的地点坐标`}
        </p>
      </figure>
    )
  }
  return (
    <figure className="animate-fade-in overflow-hidden rounded-2xl border border-[#dae7e5] bg-white shadow-card">
      <figcaption className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-[#dae7e5] px-4 py-2 text-xs font-medium text-[#183037]">
        <Route size={13} style={{ color: TP.accent }} aria-hidden />
        第 {selectedDay} 天路线示意
        <span className="font-normal text-[#5c7074]">
          滚轮缩放 · 拖拽平移 · 连线按当天到访顺序，非实际道路
        </span>
        {missingLocation && <span className="ml-auto text-red-600">部分位置暂无核实数据</span>}
      </figcaption>
      {/* 高度放大：一屏布局下中栏独立滚动，地图吃足空间（自适应取景由 fitBounds 保证） */}
      <div ref={containerRef} className="h-[360px] w-full bg-[#f5faf9]" role="img" aria-label="行程路线交互地图" />
    </figure>
  )
}

// ── 逐日时间轴（单日卡片，日卡片条选中后展示） ───────────────

const KIND_STYLE: Record<string, { dot: string; label: string; icon: React.ReactNode }> = {
  visit: { dot: '#087b73', label: '', icon: null },
  meal: { dot: '#d97706', label: '用餐', icon: <UtensilsCrossed size={11} aria-hidden /> },
  rest: { dot: '#059669', label: '休息', icon: <Coffee size={11} aria-hidden /> },
}

function ItemTags({ item }: { item: ItineraryItem }) {
  const ticket = item.poi && item.kind === 'visit' ? item.poi.ticket_cny : 0
  const stay = formatDuration(item.minutes ?? 0)
  const factStatus = item.poi
    ? classifyFact({ source: item.poi.source, verification_status: item.poi.verification_status })
    : 'unknown'
  return (
    <span className="ml-1.5 inline-flex flex-wrap items-center gap-1 align-middle">
      {/* 停留时长：排在类型之后的第一个 chip，回答「在这待多久」 */}
      {stay && (
        <span className="rounded bg-[#087b73]/10 px-1.5 py-0.5 text-[10px] text-[#087b73]">
          停留 {stay}
        </span>
      )}
      {item.wait_minutes > 0 && (
        <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
          排队 {item.wait_minutes}′
        </span>
      )}
      {ticket > 0 && factStatus === 'verified' && (
        <span className="rounded bg-[#f5faf9] px-1.5 py-0.5 text-[10px] text-[#5c7074]">
          门票 ¥{ticket.toFixed(0)} · 已核实
        </span>
      )}
      {item.poi && factStatus !== 'verified' && (
        <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-600">
          门票暂无数据
        </span>
      )}
    </span>
  )
}

// ── 时间轴交通段（leg 交错进时间轴：两个地点之间「怎么走、多久、多少钱」） ──

/** 后端 TransitLeg.mode 目前只有 walk / drive 两种（models/itinerary.py）；未知值原样展示。 */
const LEG_MODE_LABEL: Record<string, string> = {
  walk: '步行',
  drive: '驾车',
}

/**
 * 时间轴上的交通段行：mode/时长/距离/费用全部来自 day.legs 真实字段。
 * 诚实口径：未核实的路段不显示具体时长与金额（只给 mode + 提示），
 * is_estimate 标「估算」，traffic_aware 标「实时路况」。
 */
function LegRow({ leg }: { leg: TransitLeg }) {
  const fact = classifyFact({ source: leg.source })
  const verified = fact === 'verified'
  const mode = LEG_MODE_LABEL[leg.mode] ?? leg.mode
  const parts: string[] = []
  if (verified) {
    parts.push(formatDuration(leg.minutes))
    if (leg.distance_km > 0) parts.push(`${leg.distance_km.toFixed(1)}km`)
    if (leg.cost_cny > 0) parts.push(`¥${leg.cost_cny.toFixed(0)}`)
  }
  return (
    <div
      className="my-0.5 ml-2 flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg bg-[#f5faf9]/70 px-2 py-1 text-[10px] text-[#8aa0a4]"
      aria-label={`${leg.from_title}到${leg.to_title}的交通`}
    >
      <span aria-hidden className="text-[#c3d6d2]">└─</span>
      <span className="font-medium text-[#5c7074]">{mode}</span>
      {verified
        ? <span>{parts.join(' · ')}</span>
        : <span className="text-red-600">路段数据暂无核实</span>}
      {leg.traffic_aware && verified && <span>实时路况</span>}
      {leg.is_estimate && verified && <span className="text-amber-600">估算值</span>}
    </div>
  )
}

function DayCard({ day }: { day: ItineraryDay }) {
  const load = dayLoad(day)
  // legs[i] 若与 items[i].title 对上 → 交错渲染进时间轴；对不上的（异常/多余）留到底部块，不丢数据。
  const legForItem = new Map<number, TransitLeg>()
  const leftoverLegs: TransitLeg[] = []
  ;(day.legs ?? []).forEach((leg, i) => {
    if (i < day.items.length - 1 && leg.from_title === day.items[i]?.title) {
      legForItem.set(i, leg)
    } else {
      leftoverLegs.push(leg)
    }
  })
  return (
    <section className="animate-fade-in overflow-hidden rounded-2xl border border-[#dae7e5] bg-white shadow-card">
      <header className="flex items-center gap-3 border-b border-[#dae7e5] bg-[#f5faf9] px-4 py-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#087b73]/10 text-[11px] font-bold text-[#087b73]">
          D{day.day_index}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-[#183037]">第 {day.day_index} 天</h3>
          <p className="truncate text-[11px] text-[#5c7074]">{formatDayDate(day.day_date)}</p>
        </div>
        <div className="shrink-0 text-right text-[11px] leading-4 text-[#5c7074]">
          <div>活动 {day.active_minutes}′ · 在途 {day.transit_minutes}′</div>
          <div className="font-semibold text-red-600">费用暂无数据</div>
        </div>
      </header>

      {/* 负载条：活动 vs 在途的时间占比（真实派生；空日不画假条） */}
      {load.activeShare + load.transitShare > 0 && (
        <div className="flex h-1 w-full" role="img" aria-label={`活动占 ${Math.round(load.activeShare * 100)}%`}
          title={`活动 ${Math.round(load.activeShare * 100)}% · 在途 ${Math.round(load.transitShare * 100)}%`}>
          <span className="bg-[#087b73]" style={{ width: `${load.activeShare * 100}%` }} />
          <span className="bg-[#f59e0b]" style={{ width: `${load.transitShare * 100}%` }} />
        </div>
      )}

      <ol className="relative ml-6 border-l border-[#dae7e5] py-3 pl-4 pr-4">
        {day.items.map((item, idx) => {
          const style = KIND_STYLE[item.kind] ?? { dot: '#cbd5e1', label: '', icon: null }
          const leg = legForItem.get(idx)
          return (
            <Fragment key={`${item.title}-${idx}`}>
              <li className={`relative rounded-lg px-2 py-1 transition-colors [margin-left:-0.5rem] hover:bg-[#f5faf9] ${idx === day.items.length - 1 ? '' : 'pb-2.5'}`}>
                <span
                  className="absolute -left-[21px] top-2.5 h-2 w-2 rounded-full ring-2 ring-white"
                  style={{ background: style.dot }}
                  aria-hidden
                />
                <div className="flex items-baseline gap-2">
                  <span className="w-[92px] shrink-0 font-mono text-[11px] tabular-nums text-[#5c7074]">
                    {item.start}-{item.end}
                  </span>
                  <span className="min-w-0 flex-1 text-sm text-[#183037]">
                    {item.title}
                    {style.label && (
                      <span className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-[#f5faf9] px-1.5 py-0.5 text-[10px] text-[#5c7074]">
                        {style.icon}
                        {style.label}
                      </span>
                    )}
                    <ItemTags item={item} />
                  </span>
                </div>
                {item.note && <p className="mt-0.5 pl-[100px] text-[11px] text-[#5c7074]">{item.note}</p>}
              </li>
              {/* 交通段：紧贴在当前地点之后、下一个地点之前，回答「之间怎么走」 */}
              {leg && <LegRow leg={leg} />}
            </Fragment>
          )
        })}
      </ol>

      {leftoverLegs.length > 0 && (
        <div className="border-t border-[#dae7e5] px-4 py-2.5">
          <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-semibold text-[#5c7074]">
            <Route size={11} className="text-[#087b73]" aria-hidden />
            其他路段信息
          </div>
          <ul className="space-y-1.5">
            {leftoverLegs.map((leg, index) => {
              const fact = classifyFact({ source: leg.source })
              const sourceLabel = fact === 'verified' ? '已核实' : '路线数据暂无核实'
              return (
                <li key={`${leg.from_title}-${leg.to_title}-${index}`} className="flex items-center gap-2 text-[10px] text-[#5c7074]">
                  <span className="min-w-0 flex-1 truncate">{leg.from_title} → {leg.to_title}</span>
                  <span className={`shrink-0 ${fact === 'verified' ? '' : 'text-red-600'}`}>
                    {fact === 'verified' ? formatDuration(leg.minutes) : '暂无数据'} · {sourceLabel}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </section>
  )
}

// ── 日卡片条（多天行程的可切换卡片，替代原 chip Tab） ─────────

/**
 * 单张日卡片：D{n} 徽章 + 日期 + 当天亮点（前 2 个真实到访地点）+
 * 地点/用餐计数。亮点只取行程里真实出现的名称（dayVisitTitles），
 * 不生成主题标签——后端契约里没有当天主题字段，不伪造。
 */
function DayTabCard({
  day, selected, onSelect, index = 0,
}: {
  day: ItineraryDay
  selected: boolean
  onSelect: (dayIndex: number) => void
  /** 生成完成后的逐张点亮：按序号错开淡入起点（backwards 保证等待期不可见） */
  index?: number
}) {
  const highlights = dayVisitTitles(day).slice(0, 2)
  const mealCount = dayMealItems(day).length
  const visitCount = dayVisitTitles(day).length
  const load = dayLoad(day)
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      onClick={() => onSelect(day.day_index)}
      className={`group w-[172px] shrink-0 cursor-pointer overflow-hidden rounded-xl border p-3 text-left transition-all animate-fade-in ${
        selected
          ? 'border-[#087b73] bg-[#087b73]/[0.06] shadow-card ring-1 ring-[#087b73]/30'
          : 'border-[#dae7e5] bg-white hover:border-[#087b73]/40 hover:shadow-card'
      }`}
      style={{ animationDelay: `${Math.min(index, 8) * 70}ms`, animationFillMode: 'backwards' }}
    >
      <div className="flex items-center gap-2">
        <span
          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[11px] font-bold transition-colors ${
            selected ? 'bg-[#087b73] text-white' : 'bg-[#087b73]/10 text-[#087b73]'
          }`}
        >
          D{day.day_index}
        </span>
        <span className={`min-w-0 flex-1 truncate text-[11px] ${selected ? 'font-semibold text-[#087b73]' : 'text-[#5c7074]'}`}>
          {formatDayDate(day.day_date)}
        </span>
        {selected && (
          <span className="shrink-0 rounded-full bg-[#087b73] px-1.5 py-0.5 text-[9px] font-medium text-white">
            查看中
          </span>
        )}
      </div>
      <p className={`mt-2 min-h-[2em] text-xs leading-[1.4] ${selected ? 'font-medium text-[#183037]' : 'text-[#183037]/80'}`}>
        {highlights.length > 0
          ? highlights.join(' · ')
          : <span className="text-[#8fa5a3]">当天暂无到访安排</span>}
      </p>
      <div className="mt-2 flex items-center gap-1.5 text-[10px] text-[#5c7074]">
        <span>{visitCount} 个地点</span>
        {mealCount > 0 && <span className="text-[#d97706]">· {mealCount} 次用餐</span>}
      </div>
      {load.activeShare + load.transitShare > 0 && (
        <div className="mt-1.5 flex h-[3px] w-full overflow-hidden rounded-full bg-[#f5faf9]" aria-hidden>
          <span className="bg-[#087b73]/70" style={{ width: `${load.activeShare * 100}%` }} />
          <span className="bg-[#f59e0b]/70" style={{ width: `${load.transitShare * 100}%` }} />
        </div>
      )}
    </button>
  )
}

// ── 当日重点：地点卡与用餐（时间轴之外的分区重点展示） ────────

/** 当天到访地点的重点卡：名称、时段、停留、门票、备注（全部来自真实字段）。 */
function DaySpotCards({ day }: { day: ItineraryDay }) {
  const visits = day.items.filter((item) => item.kind === 'visit')
  if (visits.length === 0) return null
  return (
    <section aria-label={`第 ${day.day_index} 天地点`} className="space-y-2">
      <h4 className="flex items-center gap-1.5 text-xs font-semibold text-[#183037]">
        <MapPin size={12} className="text-[#087b73]" aria-hidden />
        当天地点 · {visits.length} 处
      </h4>
      <div className="grid gap-2 sm:grid-cols-2">
        {visits.map((item, index) => {
          const factStatus = item.poi
            ? classifyFact({ source: item.poi.source, verification_status: item.poi.verification_status })
            : 'unknown'
          const ticket = item.poi?.ticket_cny ?? 0
          return (
            <article
              key={`${item.title}-${index}`}
              className="animate-fade-in rounded-xl border border-[#dae7e5] bg-white p-3 transition-colors hover:border-[#087b73]/40"
            >
              <div className="flex items-start gap-2">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#087b73]/10 text-[10px] font-bold text-[#087b73]">
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <h5 className="truncate text-sm font-semibold text-[#183037]">{item.title}</h5>
                  <p className="mt-0.5 font-mono text-[10px] tabular-nums text-[#5c7074]">{item.start}-{item.end}</p>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {item.minutes > 0 && (
                  <span className="rounded bg-[#087b73]/10 px-1.5 py-0.5 text-[10px] text-[#087b73]">
                    停留 {formatDuration(item.minutes)}
                  </span>
                )}
                {ticket > 0 && factStatus === 'verified' && (
                  <span className="rounded bg-[#f5faf9] px-1.5 py-0.5 text-[10px] text-[#5c7074]">
                    门票 ¥{ticket.toFixed(0)} · 已核实
                  </span>
                )}
                {item.poi && factStatus !== 'verified' && (
                  <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-600">门票暂无数据</span>
                )}
              </div>
              {item.note && <p className="mt-1.5 text-[11px] leading-relaxed text-[#5c7074]">{item.note}</p>}
            </article>
          )
        })}
      </div>
    </section>
  )
}

/** 当天用餐安排（kind=meal；后端按作息插入，无真实商户时不挂假店名）。 */
function DayMealSection({ day }: { day: ItineraryDay }) {
  const meals = day.items.filter((item) => item.kind === 'meal')
  if (meals.length === 0) return null
  return (
    <section aria-label={`第 ${day.day_index} 天用餐`} className="space-y-2">
      <h4 className="flex items-center gap-1.5 text-xs font-semibold text-[#183037]">
        <UtensilsCrossed size={12} className="text-[#d97706]" aria-hidden />
        当天用餐 · {meals.length} 次
      </h4>
      <ul className="space-y-1.5">
        {meals.map((item, index) => (
          <li
            key={`${item.title}-${index}`}
            className="flex items-center gap-2.5 rounded-xl border border-[#f0dfc8] bg-[#fffaf2] px-3 py-2"
          >
            <span className="w-[92px] shrink-0 font-mono text-[11px] tabular-nums text-[#b36a2d]">{item.start}-{item.end}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-[#183037]">{item.title}</span>
            {item.note && <span className="hidden max-w-[45%] truncate text-[10px] text-[#8c7258] sm:block">{item.note}</span>}
          </li>
        ))}
      </ul>
      <p className="text-[10px] text-[#8c7258]">
        想吃点具体的？在右侧「旅行助手」问一句，比如「第一天附近有什么必吃美食」，会调真实商户检索。
      </p>
    </section>
  )
}

// ── 概览条 ───────────────────────────────────────────────────

/**
 * 行程状态机（后端 travel/models/itinerary.py 的 PLAN_STATUS_*）：
 * ready 是常态不挂角标；degraded 表示部分外部数据没取到、行程仍可用。
 */
const STATUS_LABEL: Record<string, string> = {
  ready: '',
  validating: '',
  degraded: '部分数据未取到',
  needs_user_decision: '有需要你拍板的点',
}

/** 费用分类的展示形态（条形颜色 + 图例图标）。顺序 = costBreakdown 的切片顺序。 */
const COST_META: Record<string, { bar: string; icon: React.ReactNode }> = {
  tickets: { bar: '#087b73', icon: <Ticket size={12} aria-hidden /> },
  meals: { bar: '#d97706', icon: <UtensilsCrossed size={12} aria-hidden /> },
  lodging: { bar: '#0d9488', icon: <BedDouble size={12} aria-hidden /> },
  transit: { bar: '#94a3b8', icon: <TrainFront size={12} aria-hidden /> },
}

function OverviewBar({
  itinerary, planStatus, exporting, feedbackSent, onExportIcs, onFeedback,
}: {
  itinerary: Itinerary
  planStatus?: string
  exporting: boolean
  feedbackSent: '' | 'positive' | 'negative'
  onExportIcs: () => void
  onFeedback: (vote: 'positive' | 'negative') => void
}) {
  const { brief, cost } = itinerary
  const { slices } = costBreakdown(cost)
  const availability = costAvailability(itinerary)

  // 需求摘要 chips：右侧「旅行助手」要改的就是这几项，看不见就没法改。
  const meta: string[] = []
  if (brief.party_size) meta.push(`${brief.party_size} 人`)
  meta.push(`节奏${PACE_LABEL[brief.pace] ?? '适中'}`)
  if (brief.preferences.length) meta.push(`偏好 ${brief.preferences.join('、')}`)
  if (brief.must_go.length) meta.push(`必去 ${brief.must_go.join('、')}`)
  if (brief.avoid?.length) meta.push(`避开 ${brief.avoid.join('、')}`)
  // 忌口必须露出来：它是「不能出错」的约束，藏起来用户根本不知道系统记住了
  if (brief.diet) meta.push(`忌口 ${brief.diet}`)

  const badge = departureBadge(brief.start_date)

  return (
    <section
      className="animate-fade-in rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card"
      role="region"
      aria-live="polite"
      aria-label={`当前行程 · ${brief.destination || '未命名目的地'} · ${itinerary.days.length} 天 · v${itinerary.plan_version}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-[#183037]">
              {brief.destination || '未命名目的地'} · {itinerary.days.length} 天
            </h2>
            <span className="rounded-full border border-[#dae7e5] bg-[#f5faf9] px-2 py-0.5 text-[11px] text-[#183037]">
              {planStatus === 'waiting_confirmation' ? '待确认' : '当前行程'} · v{itinerary.plan_version}
            </span>
            {/* 出发倒计时：真实派生；无日期不渲染（「未定日期」在逐日卡里已有） */}
            {badge && (
              <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium text-white"
                style={{ background: TP.accent }}>
                <CalendarPlus size={11} aria-hidden />
                {badge}
              </span>
            )}
            {STATUS_LABEL[itinerary.status] && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">
                {STATUS_LABEL[itinerary.status]}
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {brief.start_date && (
              <span className="rounded-full border border-[#dae7e5] bg-[#f5faf9] px-2 py-0.5 text-[11px] text-[#5c7074]">
                {brief.start_date} 出发
              </span>
            )}
            {meta.map((m) => (
              <span
                key={m}
                className="rounded-full border border-[#dae7e5] bg-[#f5faf9] px-2 py-0.5 text-[11px] text-[#5c7074]"
              >
                {m}
              </span>
            ))}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={onExportIcs}
            disabled={exporting}
            className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-[#dae7e5] px-2.5 py-1.5
              text-xs text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037]
              disabled:cursor-not-allowed disabled:opacity-50"
          >
            <CalendarPlus size={13} aria-hidden />
            {exporting ? '导出中…' : '导出日历'}
          </button>
          <button
            type="button"
            onClick={() => onFeedback('positive')}
            disabled={!!feedbackSent}
            title="这份行程有用"
            aria-label="这份行程有用"
            className={`flex cursor-pointer items-center rounded-lg border px-2 py-1.5 text-xs transition-colors ${
              feedbackSent === 'positive'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                : 'border-[#dae7e5] text-[#5c7074] hover:border-[#087b73]/40 hover:text-[#183037]'
            }`}
          >
            <ThumbsUp size={13} aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => onFeedback('negative')}
            disabled={!!feedbackSent}
            title="需要改"
            aria-label="需要改"
            className={`flex cursor-pointer items-center rounded-lg border px-2 py-1.5 text-xs transition-colors ${
              feedbackSent === 'negative'
                ? 'border-red-300 bg-red-50 text-red-700'
                : 'border-[#dae7e5] text-[#5c7074] hover:border-[#087b73]/40 hover:text-[#183037]'
            }`}
          >
            <ThumbsDown size={13} aria-hidden />
          </button>
        </div>
      </div>

      <div className="mt-3">
        <div className="flex items-center gap-3 rounded-xl border border-[#dae7e5] bg-[#f5faf9] px-4 py-3">
          <Wallet size={18} className="shrink-0" style={{ color: TP.accent }} aria-hidden />
          <div className="min-w-0">
            <p className="text-[10px] text-[#5c7074]">总花费</p>
            <p className={`text-2xl font-bold leading-7 tabular-nums ${availability.total === 'verified' ? 'text-[#087b73]' : 'text-red-600'}`}>
              {availability.total === 'verified' ? '费用已核实' : '暂无数据'}
            </p>
            {availability.total !== 'verified' && <p className="text-[10px] text-red-600">未接入可核验费用来源</p>}
          </div>
        </div>
        <details className="mt-2 rounded-xl border border-[#dae7e5] bg-white px-4 py-2.5">
          <summary className="cursor-pointer text-xs font-medium text-[#183037]">查看费用明细</summary>
          <dl className="mt-2 grid gap-2 sm:grid-cols-2">
            {slices.map((slice) => {
              const status = availability[slice.key as keyof typeof availability]
              return (
                <div key={slice.key} className="flex items-center justify-between gap-2 text-[11px]">
                  <dt className="flex items-center gap-1.5 text-[#5c7074]">
                    <span className="h-2 w-2 rounded-full" style={{ background: COST_META[slice.key]?.bar ?? '#cbd5e1' }} aria-hidden />
                    {COST_META[slice.key]?.icon}
                    {slice.label}
                  </dt>
                  <dd className={status === 'verified' ? 'text-[#183037]' : 'text-red-600'}>
                    {status === 'verified' ? `¥${slice.value.toFixed(0)} · 已核实` : '暂无数据'}
                  </dd>
                </div>
              )
            })}
          </dl>
        </details>
      </div>
    </section>
  )
}

// ── 导出给页面用 ─────────────────────────────────────────────

export interface ItineraryViewProps {
  itinerary: Itinerary
  conversationId: string
  planStatus?: string
  /** 追问 / 失败提示：有行程时也展示，不顶掉行程 */
  notice?: string
  finalAnswer?: string
  exporting?: boolean
  feedbackSent: '' | 'positive' | 'negative'
  /** 当前选中天（受控）：页面传入后与右侧助手共享「正在看第几天」 */
  selectedDay?: number | null
  onSelectedDayChange?: (dayIndex: number) => void
  onExportIcs: () => void
  onFeedback: (vote: 'positive' | 'negative') => void
  onPlanResponse: (data: PlanResponse) => void
}

export default function ItineraryView({
  itinerary, conversationId, planStatus = '', notice, finalAnswer, exporting = false,
  feedbackSent, selectedDay: selectedDayProp, onSelectedDayChange, onExportIcs, onFeedback, onPlanResponse,
}: ItineraryViewProps) {
  const dayCount = itinerary.days.length
  // 受控优先（页面要跟右侧助手共享选中天）；未传时退回内部自管。
  const [innerDay, setInnerDay] = useState(1)
  const selectedDay = selectedDayProp ?? innerDay
  const setSelectedDay = (dayIndex: number) => {
    if (onSelectedDayChange) onSelectedDayChange(dayIndex)
    else setInnerDay(dayIndex)
  }
  // 选中天默认取返回结果的第一天；后端历史数据的 day_index 不一定从 1 开始。
  const activeDayData = itinerary.days.find((d) => d.day_index === selectedDay) ?? itinerary.days[0]
  const activeDay = activeDayData?.day_index ?? 1
  // 当日亮点 chips：全部从真实字段派生——必去=前 2 个到访地点、美食=当天用餐、
  // 提示=当天存在估算路段。后端没有「当天主题」字段，不伪造标签。
  const dayHighlights = useMemo(() => {
    if (!activeDayData) return [] as Array<{ text: string; tone: 'spot' | 'meal' | 'tip' }>
    const chips: Array<{ text: string; tone: 'spot' | 'meal' | 'tip' }> = []
    dayVisitTitles(activeDayData).slice(0, 2).forEach((title) => chips.push({ text: `必去 ${title}`, tone: 'spot' }))
    dayMealItems(activeDayData).slice(0, 2).forEach((meal) => chips.push({ text: meal.title, tone: 'meal' }))
    if ((activeDayData.legs ?? []).some((leg) => leg.is_estimate)) {
      chips.push({ text: '部分路段为估算值', tone: 'tip' })
    }
    return chips
  }, [activeDayData])
  const availability = costAvailability(itinerary)
  const visitItems = (itinerary.days ?? []).flatMap((day) => (
    (day.items ?? []).filter((item) => item.kind === 'visit')
  ))
  const coordinatesAvailable = visitItems.length > 0 && visitItems.every((item) => (
    item.poi != null
      && Number.isFinite(item.poi.lat)
      && Number.isFinite(item.poi.lng)
      && classifyFact({ source: item.poi.source, verification_status: item.poi.verification_status }) === 'verified'
  ))
  const legs = (itinerary.days ?? []).flatMap((day) => day.legs ?? [])
  const routeAvailable = legs.length > 0 && legs.every((leg) => (
    !leg.is_estimate && classifyFact({ source: leg.source }) === 'verified'
  ))
  useEffect(() => {
    if (itinerary.days.length > 0 && !itinerary.days.some((day) => day.day_index === selectedDay)) {
      setSelectedDay(itinerary.days[0].day_index)
    }
  }, [itinerary.days, selectedDay])
  const [versionOpen, setVersionOpen] = useState(false)
  const [versions, setVersions] = useState<TravelPlanVersion[]>([])
  const [diff, setDiff] = useState<TravelPlanDiff | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [localPlanStatus, setLocalPlanStatus] = useState(planStatus)

  useEffect(() => setLocalPlanStatus(planStatus), [planStatus])

  const loadVersions = async () => {
    if (historyLoading) return
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const result = await fetchTravelPlanVersions(conversationId)
      setVersions(result.versions ?? [])
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : '版本历史暂不可用')
    } finally {
      setHistoryLoading(false)
    }
  }

  const toggleVersions = () => {
    const next = !versionOpen
    setVersionOpen(next)
    if (next && versions.length === 0) void loadVersions()
  }

  const confirmCurrent = async () => {
    setActionLoading(true)
    setHistoryError('')
    try {
      await confirmTravelPlan(conversationId, itinerary.plan_version)
      setLocalPlanStatus('confirmed')
      onPlanResponse({
        status: 'ready', final_answer: '', itinerary, plan_status: 'confirmed',
      })
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : '确认失败，请刷新后重试')
    } finally {
      setActionLoading(false)
    }
  }

  const inspectDiff = async (version: number) => {
    setHistoryError('')
    try {
      setDiff(await fetchTravelPlanDiff(conversationId, version, itinerary.plan_version))
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : '版本差异暂不可用')
    }
  }

  const restoreVersion = async (targetVersion: number) => {
    setActionLoading(true)
    setHistoryError('')
    try {
      const result = await restoreTravelPlan(conversationId, targetVersion, itinerary.plan_version)
      onPlanResponse({
        status: 'ready', final_answer: `已从 v${targetVersion} 恢复为新版本。`,
        itinerary: result.itinerary, plan_status: result.plan_status,
      })
      setLocalPlanStatus(result.plan_status)
      setVersions([])
      setDiff(null)
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : '恢复失败，请刷新后重试')
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      {notice && (
        <div className="flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden />
          <p className="min-w-0 flex-1 whitespace-pre-wrap">{notice}</p>
        </div>
      )}

      <OverviewBar
        itinerary={itinerary}
        planStatus={localPlanStatus}
        exporting={exporting}
        feedbackSent={feedbackSent}
        onExportIcs={onExportIcs}
        onFeedback={onFeedback}
      />

      <section className="rounded-2xl border border-[#dae7e5] bg-white shadow-card">
        <div className="flex flex-wrap items-center justify-end gap-2 px-4 py-3">
          {localPlanStatus === 'waiting_confirmation' && (
            <button
              type="button"
              onClick={() => void confirmCurrent()}
              disabled={actionLoading}
              className="ml-auto inline-flex items-center gap-1 rounded-lg bg-[#087b73] px-2.5 py-1.5 text-[11px] text-white hover:bg-[#06655f] disabled:opacity-50"
            >
              <Check size={12} /> 确认当前行程
            </button>
          )}
          <button
            type="button"
            onClick={toggleVersions}
            className="inline-flex items-center gap-1 rounded-lg border border-[#dae7e5] px-2.5 py-1.5 text-[11px] text-[#5c7074] hover:text-[#183037]"
          >
            <History size={12} /> {versionOpen ? '收起版本' : '版本历史'}
          </button>
        </div>
        {versionOpen && (
          <div className="border-t border-[#dae7e5] px-4 py-3">
            {historyLoading && <p className="text-xs text-[#5c7074]">正在读取版本历史…</p>}
            {!historyLoading && versions.length === 0 && !historyError && (
              <p className="text-xs text-[#5c7074]">暂无可用版本历史。</p>
            )}
            {historyError && <p className="text-xs text-red-600">{historyError}</p>}
            <ul className="space-y-2">
              {versions.map((version) => (
                <li key={version.plan_version} className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium text-[#183037]">v{version.plan_version}</span>
                  <span className="text-[#7a8e8b]">{version.plan_status === 'confirmed' ? '已确认' : '待确认'}</span>
                  <span className="min-w-0 flex-1 truncate text-[#7a8e8b]">{version.destination || '未命名行程'}</span>
                  {version.plan_version !== itinerary.plan_version && (
                    <>
                      <button type="button" onClick={() => void inspectDiff(version.plan_version)} className="text-[#087b73] hover:underline">查看差异</button>
                      <button type="button" onClick={() => void restoreVersion(version.plan_version)} disabled={actionLoading} className="inline-flex items-center gap-1 text-[#087b73] hover:underline disabled:opacity-50"><Undo2 size={11} />恢复</button>
                    </>
                  )}
                </li>
              ))}
            </ul>
            {diff && (
              <div className="mt-3 rounded-lg bg-[#f5faf9] px-3 py-2 text-[10px] text-[#5c7074]">
                v{diff.from_version} → v{diff.to_version}：新增 {diff.added.length} 项，移除 {diff.removed.length} 项，移动 {diff.moved.length} 项；需求字段变化 {diff.brief_fields.length} 项。
              </div>
            )}
          </div>
        )}
      </section>

      <details className="rounded-2xl border border-[#dae7e5] bg-white px-4 py-3 shadow-card">
        <summary className="cursor-pointer text-xs font-semibold text-[#183037]">数据说明 <span className="ml-2 font-normal text-red-600">缺失数据以红色标记</span></summary>
        <div className="mt-2 grid gap-1 text-[11px] text-[#5c7074] sm:grid-cols-2">
          <span className={availability.total === 'verified' ? '' : 'text-red-600'}>费用：{availability.total === 'verified' ? '已核验' : '暂无数据'}</span>
          <span className={availability.tickets === 'verified' ? '' : 'text-red-600'}>门票：{availability.tickets === 'verified' ? '已核验' : '暂无数据'}</span>
          <span className={routeAvailable ? '' : 'text-red-600'}>路线：{routeAvailable ? '已核验' : '暂无数据'}</span>
          <span className={coordinatesAvailable ? '' : 'text-red-600'}>坐标：{coordinatesAvailable ? '已核验' : '暂无数据'}</span>
        </div>
        <p className="mt-2 text-[10px] text-[#7a8e8b]">只有返回可核验来源的字段才会显示具体数值；其余统一标记为暂无数据。</p>
      </details>

      {/* 城际班次条（B 方案）：itinerary.intercity 有数据才渲染；诚实口径注记 */}
      {itinerary.intercity && itinerary.intercity.length > 0 && (
        <section
          aria-label="城际交通"
          className="animate-fade-in rounded-2xl border border-[#dae7e5] bg-[#f5faf9] px-4 py-3 shadow-card"
        >
          <header className="flex flex-wrap items-center gap-2">
            <TrainFront size={14} style={{ color: TP.accent }} aria-hidden />
            <h3 className="text-sm font-semibold text-[#183037]">
              城际交通 · {itinerary.intercity[0]?.from_station || ''}
              {itinerary.intercity[0]?.from_station ? ' → ' : ''}
              {itinerary.intercity[0]?.to_station || ''}
            </h3>
            <span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-[#5c7074]">
              {itinerary.intercity[0]?.date || ''} · 12306 实时检索
            </span>
          </header>
          <ul className="mt-2 space-y-1.5">
            {itinerary.intercity.map((train, index) => {
              const seatsText = Object.entries(train.seats ?? {})
                .slice(0, 2)
                .map(([seat, left]) => `${seat} ${String(left)}`)
                .join(' · ')
              const priceText = Object.entries(train.prices ?? {})
                .slice(0, 2)
                .map(([seat, price]) => {
                  const raw = String(price)
                  return `${seat} ${/^\d+(\.\d+)?$/.test(raw) ? `¥${raw}` : raw}`
                })
                .join(' · ')
              return (
                <li key={`${train.train_no}-${index}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg bg-white px-3 py-1.5 text-xs text-[#183037]">
                  <span className="font-semibold">{train.train_no}</span>
                  <span className="font-mono tabular-nums text-[#5c7074]">
                    {train.start_time || '--'} → {train.arrive_time || '--'}
                  </span>
                  {train.duration && <span className="text-[#5c7074]">{train.duration}</span>}
                  <span className="ml-auto text-[11px] text-[#5c7074]">
                    {seatsText || <span className="text-[#b3a48f]">余票 --</span>}
                    {priceText && <span className="ml-2 font-medium text-[#087b73]">{priceText}</span>}
                  </span>
                </li>
              )
            })}
          </ul>
          <p className="mt-1.5 text-[10px] text-[#8c7258]">
            余票与票价来自 12306 非官方聚合源，可能延迟；票价仅实时查询前 2 个车次，购票请以 12306 官方为准。
          </p>
        </section>
      )}

      {/* 日卡片条（可切换）+ 当日重点详情：左列时间轴/地点/用餐，右列当天地图 */}
      <section aria-label="按天查看">
        {dayCount > 1 && (
          <div
            role="tablist"
            aria-label="行程日期"
            className="mb-3 flex gap-2 overflow-x-auto pb-1.5 [scrollbar-width:thin]"
          >
            {itinerary.days.map((d, index) => (
              <DayTabCard
                key={d.day_index}
                day={d}
                selected={d.day_index === activeDay}
                onSelect={setSelectedDay}
                index={index}
              />
            ))}
          </div>
        )}
        {activeDayData && (
          <header className="mb-2.5 flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-[#183037]">
              第 {activeDayData.day_index} 天行程
            </h3>
            <span className="text-[11px] text-[#5c7074]">{formatDayDate(activeDayData.day_date)}</span>
            <span className="rounded-full bg-[#f5faf9] px-2 py-0.5 text-[10px] text-[#5c7074]">
              活动 {activeDayData.active_minutes}′ · 在途 {activeDayData.transit_minutes}′
            </span>
          </header>
        )}
        {/* 当日亮点：选中天的第一眼摘要（地点/美食/提示），随日卡片切换联动 */}
        {activeDayData && dayHighlights.length > 0 && (
          <div className="mb-2.5 flex flex-wrap items-center gap-1.5" aria-label={`第 ${activeDay} 天亮点`}>
            <span className="text-[11px] font-medium text-[#5c7074]">当日亮点</span>
            {dayHighlights.map((chip) => (
              <span
                key={chip.text}
                className={`animate-fade-in rounded-full px-2 py-0.5 text-[11px] ${
                  chip.tone === 'spot'
                    ? 'bg-[#e2f0ee] text-[#087b73]'
                    : chip.tone === 'meal'
                      ? 'bg-[#fdf1e0] text-[#b4690e]'
                      : 'bg-[#f5faf9] text-[#5c7074]'
                }`}
              >
                {chip.text}
              </span>
            ))}
          </div>
        )}
        <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1.02fr)_minmax(320px,.98fr)]">
          <div className="space-y-3">
            {activeDayData && <DayCard day={activeDayData} />}
            {activeDayData && <DaySpotCards day={activeDayData} />}
            {activeDayData && <DayMealSection day={activeDayData} />}
          </div>
          <RouteMap itinerary={itinerary} selectedDay={activeDay} />
        </div>
      </section>

      {itinerary.warnings.length > 0 && (
        // 出行须知默认折叠：内容全是「数据源未接入」的兜底说明，逐条平铺
        // 会把逐日行程挤下屏；degraded 的行程级警示不在这里，已挂概览条角标。
        <details className="group animate-fade-in rounded-2xl border border-[#dae7e5] bg-white shadow-card">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-4 py-3 text-sm font-semibold text-[#5c7074] transition-colors hover:text-[#183037] [&::-webkit-details-marker]:hidden">
            <ListChecks size={14} className="text-amber-600" aria-hidden />
            出行须知
            <span className="text-[11px] font-normal text-[#5c7074]">{itinerary.warnings.length} 条</span>
            <span
              className="ml-auto text-[#5c7074] transition-transform duration-200 group-open:rotate-180"
              aria-hidden
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>
            </span>
          </summary>
          <ul className="list-inside list-disc space-y-1 border-t border-[#dae7e5] px-4 py-3 text-sm text-[#5c7074]">
            {itinerary.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </details>
      )}

      {finalAnswer && (
        <details className="rounded-2xl border border-[#dae7e5] bg-white px-4 py-3 shadow-card">
          <summary className="cursor-pointer text-sm text-[#5c7074]">
            查看完整行程单（文本版，可复制发同行人）
          </summary>
          <div className="mt-3 text-sm">
            <MarkdownContent content={finalAnswer} />
          </div>
        </details>
      )}
    </div>
  )
}
